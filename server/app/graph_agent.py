"""Mesin agent LangGraph (AGENT_ENGINE=langgraph).

Graf yang sama perilakunya dengan loop custom di app/agent.py, tetapi setiap tahap adalah node graf:

    masuk -> praproses per jenis input (teks | suara_stt |
             struk_dedup -> struk_ocr -> [struk_aturan] -> struk_llm_teks -> struk_validasi -> [struk_vision]
             -> struk_hasil -> struk_serupa)
          -> konteks -> agent <-> tools ; agent -> guard -> (agent | selesai) ; tools -> selesai
          selesai -> tunggu_jawaban (interrupt, bila menunggu konfirmasi) -> agent

Kontrak API tidak berubah: tiap run tetap satu job, step trace bertipe sama (input, ocr, llm, reasoning, tool, guard, ...)
plus field aditif `node`. Konfirmasi memakai `interrupt()` + checkpointer SQLite: run yang bertanya berakhir
`awaiting_confirmation`; jawaban pengguna (run baru, conversation_id sama) melanjutkan thread graf yang sama lewat
`Command(resume=...)`. Tidak ada node email: fitur email dinonaktifkan pemilik (EMAIL_ENABLED=false); bila dinyalakan
lagi, run email dijalankan mesin custom (lihat app/engine.py). Bila thread tidak ditemukan (mis. pertanyaan dibuat mesin custom), node `konteks` memakai riwayat
dari database persis seperti mesin custom.
"""
from . import lc_llm  # noqa: F401  (harus dimuat pertama: mematikan tracing LangSmith sebelum LangChain dimuat)

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import sqlite3  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, TypedDict  # noqa: E402

from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.runtime import Runtime  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

from . import agent as A  # noqa: E402
from . import db, tools  # noqa: E402
from .config import get_settings  # noqa: E402
from .inputs import ocr as ocr_mod, receipt, receipt_rules, stt  # noqa: E402
from .trace import CURRENT_NODE, Tracer, publish  # noqa: E402
from .util import err  # noqa: E402

log = logging.getLogger("graph_agent")

# Kunci ctx yang berlaku per run (direset saat jawaban konfirmasi melanjutkan thread).
_PER_RUN_CTX = ("created", "ok_writes", "ok_tools", "nudged", "write_results", "failed_writes", "usage_extra")


class State(TypedDict, total=False):
    run_id: str
    conversation_id: str
    input_type: str
    payload: dict
    ctx: dict
    user_content: str
    messages: list[dict]  # tanpa system prompt (dibuat ulang tiap panggilan LLM, sama seperti mesin custom)
    step_no: int
    pending_calls: list[dict]
    last_msg: dict
    rc: dict  # data antara pipeline struk
    early: dict | None  # keluar lebih awal (duplikat / struk serupa)
    status: str | None
    answer: str | None
    outcome: str | None
    warnings: list[str]
    root_run_id: str


@dataclass
class RunContext:
    """Konteks runtime per run (tidak masuk checkpoint): tracer run aktif + akumulasi token."""
    tracer: Tracer
    totals: dict = field(default_factory=lambda: {"prompt": 0, "completion": 0, "llm_calls": 0, "tool_calls": 0})


Rt = Runtime[RunContext]


def _node(name: str, fn):
    """Bungkus node: set CURRENT_NODE agar setiap step trace mendapat field `node`."""
    async def wrapper(state: State, runtime: Rt):
        tok = CURRENT_NODE.set(name)
        try:
            return await fn(state, runtime)
        finally:
            CURRENT_NODE.reset(tok)
    wrapper.__name__ = name
    return wrapper


def _llm_call(tr: Tracer, name: str, think):
    return lambda **kw: A.llm_step(tr, name, **{"think": think, "chat_fn": lc_llm.chat, **kw})


def _early(status: str, outcome: str, answer: str, messages: list[dict] | None = None, confirm: str | None = None) -> dict:
    return {"early": {"status": status, "outcome": outcome, "answer": answer, "confirm": confirm},
            "messages": messages or []}


# ------------------------------------------------------------------ masuk & praproses

async def n_masuk(state: State, rt: Rt) -> dict:
    rt.context.tracer.add("input", state["input_type"], dict(state["payload"]),
                          {"conversation_id": state["conversation_id"]})
    return {}


def r_masuk(state: State) -> str:
    return {"text": "teks", "voice": "suara_stt", "receipt": "struk_dedup"}.get(
        state["input_type"], "teks")


async def n_teks(state: State, rt: Rt) -> dict:
    if state["input_type"] != "text":
        raise ValueError(f"input_type tidak didukung mesin langgraph: {state['input_type']}")
    return {"user_content": state["payload"]["text"]}


async def n_suara_stt(state: State, rt: Rt) -> dict:
    tr, payload, ctx = rt.context.tracer, state["payload"], dict(state["ctx"])
    async with tr.timed("preprocess", "transkripsi_suara", {"file": Path(payload["audio_path"]).name,
                                                             "engine": "faster-whisper"}) as box:
        res = await asyncio.to_thread(stt.transcribe, payload["audio_path"])
        box["output"] = res
    ctx["transcript"] = res["text"]
    if not res["text"]:
        return {"ctx": ctx, "user_content": "(Voice note kosong / tidak terdengar ucapan.)"}
    return {"ctx": ctx, "user_content": f"[Voice note pengguna, hasil transkripsi otomatis — bisa ada salah dengar]\n{res['text']}"}


def _dedup(tr: Tracer, run_id: str, key: str, label: str) -> dict | None:
    """Sama dengan agent._dedup_or_raise, tetapi mengembalikan update early-exit alih-alih raise."""
    try:
        A._dedup_or_raise(tr, run_id, key, label)
    except A.EarlyExit as ex:
        return _early(ex.status, ex.outcome, ex.answer, ex.messages)
    return None


# ------------------------------------------------------------------ pipeline struk sebagai node

def _rc_fail(state: State, rt: Rt, e: Exception) -> None:
    """Kegagalan di dalam pipeline struk: step preprocess ekstrak_struk berstatus error (sama seperti tr.timed custom)."""
    rc = state.get("rc") or {}
    t0 = rc.get("t0") or time.time()
    rt.context.tracer.add("preprocess", "ekstrak_struk", rc.get("prep_input"), {"error": err(e)}, status="error",
                          started_at=t0, duration_ms=int((time.time() - t0) * 1000))


def _struk(fn):
    async def inner(state: State, rt: Rt) -> dict:
        try:
            return await fn(state, rt)
        except Exception as e:
            _rc_fail(state, rt, e)
            raise
    return inner


async def n_struk_dedup(state: State, rt: Rt) -> dict:
    tr, payload, s = rt.context.tracer, state["payload"], get_settings()
    data = Path(payload["image_path"]).read_bytes()
    early = _dedup(tr, state["run_id"], "receipt:sha256:" + hashlib.sha256(data).hexdigest(), "Foto struk yang sama")
    if early:
        return early
    t0 = time.time()
    prep_input = {"file": Path(payload["image_path"]).name, "bytes": len(data), "receipt_mode": s.receipt_mode,
                  "fallback_vision": s.receipt_fallback_vision}
    tr.started("preprocess", "ekstrak_struk", t0)
    return {"rc": {"t0": t0, "prep_input": prep_input, "mode": s.receipt_mode, "jalur": [], "fallback": None,
                   "validasi": None, "llm_usage": [], "prompt": None, "raw": None, "reasons": [], "data": None}}


def r_struk_dedup(state: State) -> str:
    if state.get("early"):
        return "selesai"
    return "struk_vision" if state["rc"]["mode"] == "vision" else "struk_ocr"


@_struk
async def n_struk_ocr(state: State, rt: Rt) -> dict:
    tr, s, rc = rt.context.tracer, get_settings(), dict(state["rc"])
    image = Path(state["payload"]["image_path"]).read_bytes()
    t0 = time.time()
    tr.started("ocr", "ocr_struk", t0)
    try:
        ocr = await asyncio.to_thread(ocr_mod.run_ocr, image)
    except Exception as e:  # noqa: BLE001
        tr.add("ocr", "ocr_struk", {"engine": ocr_mod.ENGINE_NAME}, {"error": err(e)}, status="error", started_at=t0,
               duration_ms=int((time.time() - t0) * 1000))
        if rc["mode"] == "ocr_llm" and s.receipt_fallback_vision:
            tr.add("fallback", "fallback_vision", {"alasan": [f"OCR gagal: {err(e)}"]}, {"mode_baru": "vision"}, status="warning")
            rc["fallback"] = {"alasan": [f"OCR gagal: {err(e)}"]}
            rc["next"] = "struk_vision"
            return {"rc": rc}
        raise
    rc["jalur"] = rc["jalur"] + ["ocr"]
    tr.add("ocr", "ocr_struk", {"engine": ocr["engine"], "image_size": ocr["image_size"], "ocr_input_size": ocr["ocr_input_size"]},
           {k: ocr[k] for k in ("lines", "boxes", "n_boxes", "mean_conf", "min_conf", "skew_deg", "image_size", "coord_space",
                                 "timings_ms")},
           started_at=t0, duration_ms=ocr["timings_ms"]["total"])
    rc["ocr_full"] = ocr
    rc["ocr"] = {"mean_conf": ocr["mean_conf"], "n_lines": len(ocr["lines"]), "ms": ocr["timings_ms"]["total"]}
    rc["next"] = "struk_aturan" if (rc["mode"] == "ocr_only" or s.receipt_rules_first) else "struk_llm_teks"
    return {"rc": rc}


def r_rc_next(state: State) -> str:
    return state["rc"]["next"]


@_struk
async def n_struk_aturan(state: State, rt: Rt) -> dict:
    """Parser aturan (RECEIPT_MODE=ocr_only, atau ocr_llm + RECEIPT_RULES_FIRST)."""
    tr, s, rc = rt.context.tracer, get_settings(), dict(state["rc"])
    ocr = rc["ocr_full"]
    t1 = time.time()
    if rc["mode"] == "ocr_only":
        data = receipt_rules.parse_lines(ocr["lines"])
        tr.add("rules", "parse_aturan", {"jumlah_baris": len(ocr["lines"])}, data, started_at=t1,
               duration_ms=int((time.time() - t1) * 1000))
        rc["jalur"] = rc["jalur"] + ["rules"]
        data = receipt.normalize(data)
        v = receipt_rules.validate(data, ocr, s.ocr_min_conf)
        tr.add("validation", "validasi_struk", {"sumber": "ocr_only"}, v, status="ok" if v["lolos"] else "warning")
        rc["validasi"] = v
        data["keyakinan"] = round(ocr["mean_conf"] if v["lolos"] else min(0.5, ocr["mean_conf"]), 2)
        data["sumber_ekstraksi"] = "ocr_only"
        rc["data"], rc["next"] = data, "struk_hasil"
        return {"rc": rc}
    rdata = receipt.normalize(receipt_rules.parse_lines(ocr["lines"]))
    tr.add("rules", "parse_aturan", {"jumlah_baris": len(ocr["lines"]), "rules_first": True}, rdata, started_at=t1,
           duration_ms=int((time.time() - t1) * 1000))
    rv = receipt_rules.validate(rdata, ocr, s.ocr_min_conf)
    tr.add("validation", "validasi_struk", {"sumber": "rules_first"}, rv, status="ok" if rv["lolos"] else "warning")
    rc["jalur"] = rc["jalur"] + ["rules"]
    if rv["lolos"]:
        rc["validasi"] = rv
        rdata["keyakinan"] = round(ocr["mean_conf"], 2)
        rdata["sumber_ekstraksi"] = "ocr_rules"
        rc["data"], rc["next"] = rdata, "struk_hasil"
    else:
        rc["next"] = "struk_llm_teks"
    return {"rc": rc}


@_struk
async def n_struk_llm_teks(state: State, rt: Rt) -> dict:
    """LLM teks saja (tanpa thinking) menyusun JSON dari baris OCR."""
    rc = dict(state["rc"])
    rc["data"], rc["reasons"] = None, []
    try:
        r = await receipt._ocr_llm_extract(rc["ocr_full"], _llm_call(rt.context.tracer, "ekstrak_struk_ocr_llm", False))
        rc["llm_usage"] = rc["llm_usage"] + [r["llm"]]
        rc.update(prompt=receipt.OCR_PROMPT, raw=r["raw"])
        rc["data"] = r["data"]
        rc["jalur"] = rc["jalur"] + ["llm_teks"]
    except json.JSONDecodeError as e:
        rc["reasons"] = [f"output LLM bukan JSON: {e}"]
    return {"rc": rc}


@_struk
async def n_struk_validasi(state: State, rt: Rt) -> dict:
    """Validasi hasil LLM terhadap teks OCR; bila gagal -> fallback vision (bila diizinkan)."""
    tr, s, rc = rt.context.tracer, get_settings(), dict(state["rc"])
    data, reasons = rc["data"], list(rc["reasons"])
    if data is not None:
        v = receipt_rules.validate(data, rc["ocr_full"], s.ocr_min_conf)
        rc["validasi"] = v
        tr.add("validation", "validasi_struk", {"sumber": "ocr_llm"}, v, status="ok" if v["lolos"] else "warning")
        reasons += v["gagal"]
    rc["reasons"] = reasons
    if not reasons:
        data["sumber_ekstraksi"] = "ocr_llm"
        rc["data"], rc["next"] = data, "struk_hasil"
    elif not s.receipt_fallback_vision:
        data = data or {"toko": "", "tanggal": "", "items": [], "total": 0, "kategori": "belanja"}
        data["keyakinan"] = min(0.5, float(data.get("keyakinan") or 0.5))
        data["sumber_ekstraksi"] = "ocr_llm"
        rc["data"], rc["next"] = data, "struk_hasil"
    else:
        tr.add("fallback", "fallback_vision", {"alasan": reasons, "hasil_ocr_llm": data}, {"mode_baru": "vision"},
               status="warning")
        rc["fallback"] = {"alasan": reasons, "hasil_ocr_llm": data}
        rc["next"] = "struk_vision"
    return {"rc": rc}


@_struk
async def n_struk_vision(state: State, rt: Rt) -> dict:
    """Model vision membaca gambar: RECEIPT_MODE=vision, atau fallback dari OCR/LLM teks."""
    s, rc = get_settings(), dict(state["rc"])
    b64, meta = receipt.prepare_image(Path(state["payload"]["image_path"]).read_bytes())
    res = await receipt.extract(b64, llm_call=_llm_call(rt.context.tracer, "ekstrak_struk_vision", s.vision_think))
    rc["jalur"] = rc["jalur"] + ["vision"]
    rc["llm_usage"] = rc["llm_usage"] + [res["llm"]]
    rc.update(prompt=res["prompt"], raw=res["raw"], gambar=meta)
    data = res["data"]
    data["sumber_ekstraksi"] = "vision" if rc["mode"] == "vision" else "vision_fallback"
    rc["data"] = data
    return {"rc": rc}


async def n_struk_hasil(state: State, rt: Rt) -> dict:
    """Tutup step preprocess ekstrak_struk dan susun pesan untuk agent."""
    tr, rc, ctx = rt.context.tracer, state["rc"], dict(state["ctx"])
    data = rc["data"]
    out = {"data": data, "mode": rc["mode"], "jalur": rc["jalur"], "fallback": rc["fallback"],
           "validasi_lolos": (rc["validasi"] or {}).get("lolos"), "ocr": rc.get("ocr"),
           "raw_output": rc["raw"], "prompt": rc["prompt"], "llm": rc["llm_usage"]}
    tr.add("preprocess", "ekstrak_struk", rc["prep_input"], out, started_at=rc["t0"],
           duration_ms=int((time.time() - rc["t0"]) * 1000))
    usage = rc["llm_usage"]
    rt.context.totals["prompt"] += sum(u["prompt_tokens"] for u in usage)
    rt.context.totals["completion"] += sum(u["completion_tokens"] for u in usage)
    rt.context.totals["llm_calls"] += len(usage)
    ctx["items"] = data.get("items")
    try:
        if data.get("tanggal"):
            ctx["default_date"] = tools.parse_date(data["tanggal"])
    except tools.ToolError:
        pass
    note = state["payload"].get("note")
    content = (
        f"[Foto struk diunggah. Hasil ekstraksi struk (JSON, sumber: {data.get('sumber_ekstraksi')})]\n"
        + json.dumps(data, ensure_ascii=False)
        + "\nCatat sebagai SATU transaksi pengeluaran sebesar total struk (merchant = toko, deskripsi = ringkas item, "
        "tanggal = tanggal struk). "
        "Bila keyakinan < 0.6, total 0, atau cek_konsistensi tidak cocok, panggil minta_konfirmasi."
        + (f"\nCatatan pengguna: {note}" if note else "")
    )
    # ocr_full besar (bbox per kata) dan tidak dibutuhkan lagi: buang dari state agar checkpoint ringkas.
    return {"ctx": ctx, "user_content": content, "rc": {k: v for k, v in rc.items() if k != "ocr_full"}}


async def n_struk_serupa(state: State, rt: Rt) -> dict:
    """Anti-dobel semantik: toko + tanggal + total sama dengan struk yang sudah tercatat -> tanya dulu."""
    tr, ctx, d = rt.context.tracer, state["ctx"], state["rc"]["data"]
    similar = None
    if d.get("total") and ctx.get("default_date"):
        cands = db.query("SELECT * FROM transactions WHERE deleted=0 AND source='receipt' AND amount=? AND date=? "
                         "ORDER BY id", (d["total"], ctx["default_date"]))
        similar = next((t for t in cands if A.same_merchant(t.get("merchant"), d.get("toko"))), None)
    tr.add("dedup", "cek_struk_serupa", {"toko": d.get("toko"), "tanggal": ctx.get("default_date"), "total": d.get("total")},
           {"serupa": bool(similar), "transaksi_serupa": db.tx_row(similar) if similar else None})
    if similar:
        q = (f"Struk ini tampaknya sama dengan transaksi #{similar['id']} yang sudah tercatat "
             f"({similar['merchant']}, {similar['date']}, Rp{similar['amount']:,}). ".replace(",", ".")
             + "Apakah ini pembelian yang berbeda dan tetap mau dicatat? Jawab 'ya, catat' atau 'tidak'.")
        return _early("awaiting_confirmation", "possible_duplicate", q,
                      [{"role": "user", "content": state["user_content"]}, {"role": "assistant", "content": q}],
                      confirm="konfirmasi_duplikat")
    return {}


def r_struk_serupa(state: State) -> str:
    return "selesai" if state.get("early") else "konteks"


# ------------------------------------------------------------------ konteks, agent, tools, guard

async def n_konteks(state: State, rt: Rt) -> dict:
    """Riwayat percakapan dari database (sama dengan mesin custom) + warisan konteks bila menjawab konfirmasi."""
    tr, run_id, ctx = rt.context.tracer, state["run_id"], dict(state["ctx"])
    db.execute("UPDATE runs SET input_text=? WHERE id=?", (state["user_content"], run_id))
    history, prev = A._load_history(state["conversation_id"], run_id)
    if prev and prev["status"] == "awaiting_confirmation" and state["input_type"] == "text":
        A._inherit_pending(prev, ctx)
        tr.add("context", "lanjutan_konfirmasi", {"run_sebelumnya": prev["id"]},
               {"sumber_transaksi": ctx["source"], "run_asal": ctx["inherited_from_run"],
                "tanggal_default": ctx.get("default_date"), "jumlah_item": len(ctx.get("items") or [])})
    return {"ctx": ctx, "messages": [*history, {"role": "user", "content": state["user_content"]}], "step_no": 0}


async def n_agent(state: State, rt: Rt) -> dict:
    step_no = state.get("step_no", 0) + 1
    msgs = [{"role": "system", "content": A.system_prompt()}, *state["messages"]]
    res = await A.llm_step(rt.context.tracer, f"agent_step_{step_no}", msgs, tools=tools.TOOL_SPECS, chat_fn=lc_llm.chat)
    t = rt.context.totals
    t["prompt"] += res["prompt_tokens"]
    t["completion"] += res["completion_tokens"]
    t["llm_calls"] += 1
    msg = res["message"]
    am = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        am["tool_calls"] = msg["tool_calls"]
    return {"step_no": step_no, "messages": [*state["messages"], am], "pending_calls": msg.get("tool_calls") or [],
            "last_msg": {"content": msg.get("content")}}


def r_agent(state: State) -> str:
    return "tools" if state.get("pending_calls") else "guard"


async def n_guard(state: State, rt: Rt) -> dict:
    """Guard: klaim mencatat tanpa tool tulis -> minta model memanggil tool; jawaban kosong -> jawaban dari hasil tool."""
    tr, ctx, s = rt.context.tracer, dict(state["ctx"]), get_settings()
    raw = state["last_msg"].get("content")
    text = (raw or "").strip()
    if not ctx.get("nudged") and not ctx.get("ok_tools") and A.claims_write(text) and state["step_no"] < s.agent_max_steps:
        ctx["nudged"] = True
        tr.add("guard", "klaim_tanpa_tool", {"jawaban_model": text},
               {"tindakan": "model diminta memanggil tool", "pesan": A.NUDGE}, status="warning")
        return {"ctx": ctx, "messages": [*state["messages"], {"role": "user", "content": A.NUDGE}], "status": None}
    if not text:
        text = A.fallback_answer(ctx)
        tr.add("guard", "jawaban_kosong", {"content": raw},
               {"tindakan": "jawaban dibuat otomatis dari hasil tool", "jawaban": text}, status="warning")
    return {"ctx": ctx, "answer": text, "status": "done"}


def r_guard(state: State) -> str:
    return "selesai" if state.get("status") else "agent"


async def n_tools(state: State, rt: Rt) -> dict:
    tr, s = rt.context.tracer, get_settings()
    ctx, messages = dict(state["ctx"]), list(state["messages"])
    confirmation = None
    for call in state["pending_calls"]:
        fn = call.get("function", {})
        name = fn.get("name")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        rt.context.totals["tool_calls"] += 1
        t0 = time.time()
        try:
            result = tools.execute_tool(name, args, ctx)
            st = "ok"
        except (tools.ToolError, KeyError, ValueError, TypeError) as e:
            result = {"ok": False, "error": str(e),
                      "instruksi": "GAGAL, data BELUM tersimpan. Perbaiki argumen lalu panggil tool lagi, "
                                   "atau sampaikan kegagalan ini ke pengguna. Jangan bilang berhasil."}
            st = "error"
            if name in A.WRITE_TOOLS:
                ctx["failed_writes"] = [*ctx.get("failed_writes", []), name]
        if st == "ok" and name in A.WRITE_TOOLS:
            ctx["ok_writes"] = ctx.get("ok_writes", 0) + 1
            ctx["write_results"] = [*ctx.get("write_results", []), [name, result]]
            if name == "catat_transaksi":
                ctx["created"] = ctx.get("created", 0) + 1
        if st == "ok" and name != "minta_konfirmasi":
            ctx["ok_tools"] = ctx.get("ok_tools", 0) + 1
        tr.add("tool", name, args, result, status=st, started_at=t0, duration_ms=int((time.time() - t0) * 1000))
        messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False, default=str)})
        if name == "minta_konfirmasi":
            confirmation = args
    upd: dict[str, Any] = {"ctx": ctx, "messages": messages, "pending_calls": []}
    if confirmation:
        answer = confirmation.get("pertanyaan")
        tr.add("confirmation", "minta_konfirmasi", None, confirmation)
        messages.append({"role": "assistant", "content": answer})
        upd.update(status="awaiting_confirmation", answer=answer)
    elif state["step_no"] >= s.agent_max_steps:
        upd.update(status="done", answer="Maaf, batas langkah agent tercapai sebelum selesai.")
    return upd


def r_tools(state: State) -> str:
    return "selesai" if state.get("status") else "agent"


# ------------------------------------------------------------------ selesai & konfirmasi (interrupt)

async def n_selesai(state: State, rt: Rt) -> dict:
    tr, ctx = rt.context.tracer, state["ctx"]
    early = state.get("early")
    if early:
        if early["status"] == "awaiting_confirmation":
            tr.add("confirmation", early.get("confirm") or "konfirmasi_duplikat", None, {"pertanyaan": early["answer"]})
        tr.add("final", early["status"], None, {"answer": early["answer"], "outcome": early["outcome"], "warnings": []})
        return {"status": early["status"], "answer": early["answer"], "outcome": early["outcome"], "warnings": []}
    status, answer = state.get("status") or "done", state.get("answer")
    if status == "done" and answer and "?" in answer and not ctx.get("ok_tools") and state["input_type"] != "email":
        status = "awaiting_confirmation"
        tr.add("confirmation", "pertanyaan_teks", None,
               {"pertanyaan": answer, "catatan": "terdeteksi dari jawaban teks (heuristik), bukan dari tool minta_konfirmasi"})
    warnings = []
    if ctx.get("failed_writes") and not ctx.get("ok_writes"):
        warnings.append(f"Ada tool tulis yang gagal ({', '.join(ctx['failed_writes'])}) dan tidak ada yang berhasil; "
                        "jawaban agent mungkin tidak akurat.")
        status = "done_with_warnings"
    outcome = A._outcome(status, state["input_type"], ctx)
    tr.add("final", status, None, {"answer": answer, "outcome": outcome, "warnings": warnings})
    return {"status": status, "answer": answer, "outcome": outcome, "warnings": warnings}


def r_selesai(state: State) -> str:
    return "tunggu_jawaban" if state.get("status") == "awaiting_confirmation" else END


async def n_tunggu_jawaban(state: State, rt: Rt) -> dict:
    """Jeda graf sampai pengguna menjawab. Jawaban datang sebagai run baru (conversation_id sama) yang melanjutkan
    thread ini lewat Command(resume={"run_id", "text"}). Kode sebelum interrupt() dijalankan ulang saat resume,
    jadi tidak boleh ada efek samping di atasnya."""
    ans = interrupt({"run_id": state["run_id"], "pertanyaan": state.get("answer")})
    tr, prev_run = rt.context.tracer, state["run_id"]
    run_id, text = ans["run_id"], ans["text"]
    tr.add("input", "text", {"text": text}, {"conversation_id": state["conversation_id"]})
    db.execute("UPDATE runs SET input_text=? WHERE id=?", (text, run_id))
    ctx = {k: v for k, v in state["ctx"].items() if k not in _PER_RUN_CTX}
    root = state.get("root_run_id") or prev_run
    ctx.update(run_id=run_id, inherited_from_run=root)
    tr.add("context", "lanjutan_konfirmasi", {"run_sebelumnya": prev_run},
           {"sumber_transaksi": ctx["source"], "run_asal": root, "tanggal_default": ctx.get("default_date"),
            "jumlah_item": len(ctx.get("items") or []), "thread_graf": "dilanjutkan"})
    return {"run_id": run_id, "input_type": "text", "payload": {"text": text}, "user_content": text, "ctx": ctx,
            "messages": [*state["messages"][-30:], {"role": "user", "content": text}], "step_no": 0,
            "pending_calls": [], "early": None, "status": None, "answer": None, "outcome": None, "warnings": []}


# ------------------------------------------------------------------ rakit graf

def build_graph(checkpointer=None):
    g = StateGraph(State, context_schema=RunContext)
    nodes = {
        "masuk": n_masuk, "teks": n_teks, "suara_stt": n_suara_stt, "struk_dedup": n_struk_dedup, "struk_ocr": n_struk_ocr,
        "struk_aturan": n_struk_aturan, "struk_llm_teks": n_struk_llm_teks, "struk_validasi": n_struk_validasi,
        "struk_vision": n_struk_vision, "struk_hasil": n_struk_hasil, "struk_serupa": n_struk_serupa,
        "konteks": n_konteks, "agent": n_agent, "tools": n_tools, "guard": n_guard, "selesai": n_selesai,
        "tunggu_jawaban": n_tunggu_jawaban,
    }
    for name, fn in nodes.items():
        g.add_node(name, _node(name, fn))
    g.add_edge(START, "masuk")
    g.add_conditional_edges("masuk", r_masuk, ["teks", "suara_stt", "struk_dedup"])
    g.add_edge("teks", "konteks")
    g.add_edge("suara_stt", "konteks")
    g.add_conditional_edges("struk_dedup", r_struk_dedup, ["struk_ocr", "struk_vision", "selesai"])
    g.add_conditional_edges("struk_ocr", r_rc_next, ["struk_aturan", "struk_llm_teks", "struk_vision"])
    g.add_conditional_edges("struk_aturan", r_rc_next, ["struk_hasil", "struk_llm_teks"])
    g.add_edge("struk_llm_teks", "struk_validasi")
    g.add_conditional_edges("struk_validasi", r_rc_next, ["struk_hasil", "struk_vision"])
    g.add_edge("struk_vision", "struk_hasil")
    g.add_edge("struk_hasil", "struk_serupa")
    g.add_conditional_edges("struk_serupa", r_struk_serupa, ["konteks", "selesai"])
    g.add_edge("konteks", "agent")
    g.add_conditional_edges("agent", r_agent, ["tools", "guard"])
    g.add_conditional_edges("tools", r_tools, ["agent", "selesai"])
    g.add_conditional_edges("guard", r_guard, ["agent", "selesai"])
    g.add_conditional_edges("selesai", r_selesai, ["tunggu_jawaban", END])
    g.add_edge("tunggu_jawaban", "agent")
    return g.compile(checkpointer=checkpointer)


_mermaid: str | None = None


def mermaid() -> str:
    """Diagram Mermaid graf (teks, dibuat lokal tanpa layanan luar)."""
    global _mermaid
    if _mermaid is None:
        _mermaid = build_graph().get_graph().draw_mermaid()
    return _mermaid


# ------------------------------------------------------------------ checkpointer & pemetaan run -> thread

_saver = None
_graph = None
_conn = None
_lock = asyncio.Lock()


def _threads_db() -> sqlite3.Connection:
    c = sqlite3.connect(get_settings().langgraph_checkpoint_path, isolation_level=None)
    c.execute("CREATE TABLE IF NOT EXISTS run_threads (run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created_at REAL)")
    return c


def _thread_of(run_id: str) -> str | None:
    with _threads_db() as c:
        r = c.execute("SELECT thread_id FROM run_threads WHERE run_id=?", (run_id,)).fetchone()
    return r[0] if r else None


def _set_thread(run_id: str, thread_id: str) -> None:
    c = _threads_db()
    c.execute("INSERT OR REPLACE INTO run_threads(run_id, thread_id, created_at) VALUES (?,?,?)", (run_id, thread_id, time.time()))
    c.close()


async def get_graph():
    global _saver, _graph, _conn
    async with _lock:
        if _graph is None:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            path = Path(get_settings().langgraph_checkpoint_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            _conn = await aiosqlite.connect(str(path))
            await _conn.execute("PRAGMA journal_mode=WAL")
            _saver = AsyncSqliteSaver(_conn)
            await _saver.setup()
            _threads_db().close()
            _graph = build_graph(_saver)
    return _graph


async def shutdown() -> None:
    global _conn, _graph, _saver
    if _conn is not None:
        await _conn.close()
    _conn = _graph = _saver = None


# ------------------------------------------------------------------ satu run

async def run_agent(run_id: str) -> None:
    """Pengganti agent.run_agent untuk AGENT_ENGINE=langgraph. Menulis kolom runs dan event SSE yang sama."""
    from langsmith import tracing_context

    s = get_settings()
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    payload = db.loads(run["input_meta_json"]) or {}
    input_type = run["input_type"]
    tr = Tracer(run_id)
    t_start = time.time()
    db.execute("UPDATE runs SET status='running', started_at=?, model=?, engine='langgraph' WHERE id=?",
               (t_start, s.llm_model, run_id))
    publish(run_id, "run_status", {"run_id": run_id, "status": "running"})
    rc = RunContext(tracer=tr)
    status, answer, error, outcome, messages = "done", None, None, None, []
    try:
        graph = await get_graph()
        # Jawaban atas konfirmasi: lanjutkan thread graf yang sedang menunggu (interrupt), bila ada.
        resume_thread = None
        if input_type == "text":
            _, prev = A._load_history(run["conversation_id"], run_id)
            if prev and prev["status"] == "awaiting_confirmation":
                th = _thread_of(prev["id"])
                if th:
                    snap = await graph.aget_state({"configurable": {"thread_id": th}})
                    pend = [i for i in (snap.interrupts or ()) if (i.value or {}).get("run_id") == prev["id"]]
                    if pend:
                        resume_thread = th
        thread = resume_thread or run_id
        _set_thread(run_id, thread)
        config = {"configurable": {"thread_id": thread}, "recursion_limit": 200}
        if resume_thread:
            inp: Any = Command(resume={"run_id": run_id, "text": payload["text"]})
        else:
            inp = {"run_id": run_id, "root_run_id": run_id, "conversation_id": run["conversation_id"],
                   "input_type": input_type, "payload": payload, "ctx": {"run_id": run_id, "source": input_type},
                   "messages": [], "step_no": 0, "pending_calls": [], "early": None, "status": None, "answer": None,
                   "outcome": None, "warnings": [], "rc": {}}
        with tracing_context(enabled=False):
            final = await graph.ainvoke(inp, config, context=rc, durability="exit")
        status = final.get("status") or "done"
        answer, outcome, messages = final.get("answer"), final.get("outcome"), final.get("messages") or []
    except Exception as e:  # noqa: BLE001
        log.exception("run %s gagal (langgraph)", run_id)
        status, error, outcome = "error", err(e), "error"
        tr.add("error", type(e).__name__, None, {"error": err(e)}, status="error")

    t = rc.totals
    t_end = time.time()
    db.execute(
        """UPDATE runs SET status=?, outcome=?, final_answer=?, error=?, prompt_tokens=?, completion_tokens=?, llm_calls=?,
           tool_calls=?, finished_at=?, duration_ms=?, messages_json=? WHERE id=?""",
        (status, outcome, answer, error, t["prompt"], t["completion"], t["llm_calls"], t["tool_calls"],
         t_end, int((t_end - t_start) * 1000), db.dumps([m for m in messages if m.get("role") != "system"]), run_id),
    )
    publish(run_id, "run_finished", A.get_run_summary(run_id))

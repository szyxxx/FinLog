"""Agent pencatat keuangan: praproses input (STT / vision / email) lalu loop LLM + tool calling.

Semua langkah dicatat oleh Tracer (tabel `steps`) dan di-stream via SSE.
"""
import asyncio
import difflib
import hashlib
import json
import logging
import re
import time
from pathlib import Path

from . import db, llm, tools
from .config import get_settings
from .inputs import email_in, receipt, stt
from .trace import Tracer, publish
from .util import err

log = logging.getLogger("agent")
WRITE_TOOLS = {"catat_transaksi", "ubah_transaksi", "hapus_transaksi"}

CLAIM_RE = re.compile(
    r"(berhasil|sudah|telah)\s+(saya\s+)?(di|ter)?(catat|simpan|ubah|hapus)"
    r"|(saya|aku)\s+(sudah\s+|telah\s+)?(mencatat|menyimpan|mengubah|menghapus)"
    r"|(berhasil|sudah|telah)\s+(mencatat|menyimpan|mengubah|menghapus)", re.I)
NEG_RE = re.compile(r"\b(tidak|bukan|belum|tak|jangan|perlu|akan|ingin|mau|pernah|sebelumnya)\b", re.I)


def claims_write(text: str) -> bool:
    """True bila teks mengklaim penulisan data (abaikan bentuk negasi/pertanyaan seperti 'tidak perlu dicatat')."""
    for m in CLAIM_RE.finditer(text):
        if not NEG_RE.search(text[max(0, m.start() - 30):m.start()]):
            return True
    return False


NUDGE = ("[PESAN SISTEM] Jawabanmu mengklaim data sudah dicatat/diubah/dihapus, padahal di giliran ini belum ada "
         "pemanggilan tool tulis yang berhasil. Bila pengguna memang meminta pencatatan/perubahan, panggil tool yang sesuai "
         "sekarang. Bila pengguna menolak atau tidak meminta pencatatan (mis. menjawab 'tidak' / 'jangan dicatat'), JANGAN "
         "memanggil tool tulis; cukup tulis ulang jawabanmu tanpa mengklaim telah mencatat.")


def system_prompt() -> str:
    now = db.now_local()
    cats = "\n".join(f"- {n} ({k}): {d}" for n, k, d in db.CATEGORIES)
    return f"""Kamu adalah asisten pencatat keuangan pribadi berbahasa Indonesia.
Hari ini {now.strftime('%A')}, tanggal {now.date().isoformat()} (zona {get_settings().timezone}).

Tugasmu:
1. Mencatat pemasukan/pengeluaran dari pesan pengguna, hasil transkripsi voice note, hasil ekstraksi struk, atau email notifikasi.
2. Menjawab pertanyaan tentang keuangan dengan memakai tool (ringkasan, cari_transaksi). Jangan mengarang angka.
3. Mengubah atau menghapus transaksi bila diminta (cari dulu id-nya dengan cari_transaksi).

Aturan:
- Nominal dalam rupiah bilangan bulat: "30rb" = 30000, "4,5 juta" = 4500000, "1.250.000" = 1250000.
- Satu pesan bisa berisi beberapa transaksi: panggil catat_transaksi sekali untuk tiap transaksi.
- Tanggal relatif ("kemarin", "tadi pagi") ubah ke YYYY-MM-DD berdasarkan tanggal hari ini.
- Bila nominal, jenis (masuk/keluar), atau transaksi yang dimaksud tidak jelas, ATAU keyakinan ekstraksi rendah,
  panggil minta_konfirmasi dan JANGAN mencatat dulu. Bertanya balik ke pengguna WAJIB lewat tool minta_konfirmasi.
- Data hanya tersimpan bila kamu memanggil tool. Jangan pernah bilang "sudah dicatat" sebelum catat_transaksi berhasil.
- Email notifikasi pembayaran/transfer/invoice yang sudah berhasil/lunas = transaksi nyata: langsung catat.
- Email promo/newsletter/info yang bukan transaksi: jangan dicatat dan jangan bertanya; jawab singkat bahwa email itu bukan transaksi.
- Tagihan yang belum dibayar: tanyakan dengan minta_konfirmasi apakah sudah dibayar sebelum dicatat.
- Setelah selesai, jawab singkat (1-3 kalimat) apa yang kamu lakukan, sebutkan nominal dalam format Rp.

Kategori yang tersedia:
{cats}"""


async def llm_step(tr: Tracer, name: str, messages: list[dict], tools: list[dict] | None = None,
                   model: str | None = None, format=None, think: bool | None = None,
                   temperature: float | None = None, chat_fn=None) -> dict:
    """Satu panggilan LLM yang di-trace: potongan thinking/content di-stream (SSE `llm_delta`), lalu disimpan sebagai
    step `reasoning` (bila model berpikir) disusul step `llm` (prompt lengkap, output, token, durasi).
    `chat_fn` (opsional, signature sama dengan llm.chat) dipakai mesin LangGraph untuk memanggil model lewat ChatOllama."""
    s = get_settings()
    think = s.llm_think if think is None else think
    llm_input = {
        "model": model or s.llm_model,
        "messages": _strip_images(messages),
        "tools": [t["function"]["name"] for t in tools] if tools else None,
        "format": "json_schema" if format is not None else None,
        "options": {"think": think, "temperature": s.llm_temperature if temperature is None else temperature,
                    "num_ctx": s.llm_num_ctx, "num_predict": s.llm_num_predict},
    }
    t0 = time.time()
    tr.started("llm", name, t0)
    pub = tr.delta_publisher(name)
    try:
        res = await (chat_fn or llm.chat)(messages, tools=tools, model=model, format=format, think=think,
                                          temperature=temperature, on_delta=pub.feed)
    except Exception as e:
        pub.flush()
        tr.add("llm", name, llm_input, {"error": err(e)}, status="error", started_at=t0,
               duration_ms=int((time.time() - t0) * 1000))
        raise
    pub.flush()
    msg, tm = res["message"], res["timings_ms"]
    reasoning_seq = None
    if msg.get("thinking"):
        st = tr.add("reasoning", name, {"llm_step": name, "think": think},
                    {"thinking": msg["thinking"], "chars": len(msg["thinking"]),
                     "time_to_first_token_ms": tm.get("time_to_first_token")},
                    started_at=t0 + (tm.get("time_to_first_token") or 0) / 1000, duration_ms=tm.get("thinking"))
        reasoning_seq = st["seq"]
    tr.add("llm", name, llm_input, {
        "content": msg.get("content"),
        "tool_calls": msg.get("tool_calls"),
        "reasoning_step_seq": reasoning_seq,
        "thinking_chars": len(msg.get("thinking") or ""),
        "done_reason": res["done_reason"],
        "timings_ms": tm,
    }, started_at=t0, duration_ms=int((time.time() - t0) * 1000), prompt_tokens=res["prompt_tokens"],
        completion_tokens=res["completion_tokens"], tokens_per_second=res["tokens_per_second"])
    return res


def _strip_images(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        m = dict(m)
        if m.get("images"):
            m["images"] = [f"<gambar base64 {len(i)} karakter>" for i in m["images"]]
        out.append(m)
    return out


def _load_history(conversation_id: str, exclude_run: str) -> tuple[list[dict], dict | None]:
    row = db.query_one(
        "SELECT id, status, input_type, messages_json FROM runs WHERE conversation_id=? AND id<>? "
        "AND status IN ('done','done_with_warnings','awaiting_confirmation') ORDER BY created_at DESC LIMIT 1",
        (conversation_id, exclude_run),
    )
    hist = db.loads(row["messages_json"]) if row else None
    return (hist[-30:] if hist else []), row


def _inherit_pending(prev: dict, ctx: dict) -> None:
    """Jawaban atas minta_konfirmasi: transaksi mewarisi sumber asli (voice/receipt/email), item & tanggal struk."""
    root = prev
    while root and root["input_type"] == "text" and root["status"] == "awaiting_confirmation":
        nxt = db.query_one("SELECT id, status, input_type FROM runs WHERE conversation_id=(SELECT conversation_id FROM runs "
                           "WHERE id=?) AND created_at < (SELECT created_at FROM runs WHERE id=?) ORDER BY created_at DESC LIMIT 1",
                           (root["id"], root["id"]))
        if not nxt or nxt["status"] != "awaiting_confirmation":
            break
        root = nxt
    ctx["source"] = root["input_type"]
    ctx["inherited_from_run"] = root["id"]
    for st in db.query("SELECT name, output_json FROM steps WHERE run_id=? AND type='preprocess' AND status='ok'", (root["id"],)):
        out = db.loads(st["output_json"]) or {}
        if st["name"] == "ekstrak_struk":
            ctx["items"] = (out.get("data") or {}).get("items")
            try:
                if (out.get("data") or {}).get("tanggal"):
                    ctx["default_date"] = tools.parse_date(out["data"]["tanggal"])
            except tools.ToolError:
                pass
        elif st["name"] == "baca_email" and out.get("date"):
            ctx["default_date"] = out["date"][:10]


class EarlyExit(Exception):
    """Hentikan run sebelum loop agent (mis. duplikat). Tidak dianggap error."""

    def __init__(self, status: str, outcome: str, answer: str, messages: list[dict] | None = None) -> None:
        super().__init__(answer)
        self.status, self.outcome, self.answer, self.messages = status, outcome, answer, messages or []


def _norm_merchant(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper().replace("O", "0"))


def same_merchant(a: str | None, b: str | None) -> bool:
    x, y = _norm_merchant(a), _norm_merchant(b)
    if not x or not y:
        return not x and not y
    return x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.85


def find_duplicate_run(key: str, run_id: str) -> dict | None:
    """Run lain dengan kunci dedup yang sama dan tidak gagal (email Message-ID / hash gambar struk)."""
    return db.query_one(
        "SELECT id, status, outcome, created_at FROM runs WHERE dedup_key=? AND id<>? AND status<>'error' "
        "AND COALESCE(outcome,'')<>'duplicate' ORDER BY created_at LIMIT 1", (key, run_id))


def _dedup_or_raise(tr: Tracer, run_id: str, key: str, label: str) -> None:
    db.execute("UPDATE runs SET dedup_key=? WHERE id=?", (key, run_id))
    dup = find_duplicate_run(key, run_id)
    txs = [db.tx_row(t) for t in db.query("SELECT * FROM transactions WHERE run_id=? AND deleted=0", (dup["id"],))] if dup else []
    tr.add("dedup", "cek_duplikat", {"kunci": key}, {
        "duplikat": bool(dup), "run_sebelumnya": dup["id"] if dup else None,
        "transaksi_sebelumnya": [t["id"] for t in txs]})
    if dup:
        ids = ", ".join(f"#{t['id']} (Rp{t['amount']:,})".replace(",", ".") for t in txs) or "tidak ada transaksi"
        raise EarlyExit("done", "duplicate",
                        f"{label} ini sudah pernah diproses (run {dup['id']}; {ids}). Tidak dicatat ulang.")


async def _preprocess(run_id: str, input_type: str, payload: dict, tr: Tracer, ctx: dict) -> str:
    """Ubah input non-teks menjadi teks untuk agent. Mengembalikan isi pesan user."""
    if input_type == "text":
        return payload["text"]

    if input_type == "voice":
        async with tr.timed("preprocess", "transkripsi_suara", {"file": Path(payload["audio_path"]).name,
                                                                 "engine": "faster-whisper"}) as box:
            res = await asyncio.to_thread(stt.transcribe, payload["audio_path"])
            box["output"] = res
        ctx["transcript"] = res["text"]
        if not res["text"]:
            return "(Voice note kosong / tidak terdengar ucapan.)"
        return f"[Voice note pengguna, hasil transkripsi otomatis — bisa ada salah dengar]\n{res['text']}"

    if input_type == "receipt":
        data = Path(payload["image_path"]).read_bytes()
        _dedup_or_raise(tr, run_id, "receipt:sha256:" + hashlib.sha256(data).hexdigest(), "Foto struk yang sama")
        s_ = get_settings()

        def llm_factory(name: str, think):
            return lambda **kw: llm_step(tr, name, **{"think": think, **kw})

        async with tr.timed("preprocess", "ekstrak_struk", {"file": Path(payload["image_path"]).name, "bytes": len(data),
                                                             "receipt_mode": s_.receipt_mode,
                                                             "fallback_vision": s_.receipt_fallback_vision}) as box:
            res = await receipt.extract_receipt(data, tr, llm_factory)
            box["output"] = {"data": res["data"], "mode": res["mode"], "jalur": res["jalur"], "fallback": res["fallback"],
                             "validasi_lolos": (res["validasi"] or {}).get("lolos"), "ocr": res.get("ocr"),
                             "raw_output": res["raw"], "prompt": res["prompt"], "llm": res["llm_usage"]}
        ctx["usage_extra"] = (sum(u["prompt_tokens"] for u in res["llm_usage"]),
                              sum(u["completion_tokens"] for u in res["llm_usage"]), len(res["llm_usage"]))
        ctx["items"] = res["data"].get("items")
        try:
            if res["data"].get("tanggal"):
                ctx["default_date"] = tools.parse_date(res["data"]["tanggal"])
        except tools.ToolError:
            pass
        note = payload.get("note")
        content = (
            f"[Foto struk diunggah. Hasil ekstraksi struk (JSON, sumber: {res['data'].get('sumber_ekstraksi')})]\n"
            + json.dumps(res["data"], ensure_ascii=False)
            + "\nCatat sebagai SATU transaksi pengeluaran sebesar total struk (merchant = toko, deskripsi = ringkas item, "
            "tanggal = tanggal struk). "
            "Bila keyakinan < 0.6, total 0, atau cek_konsistensi tidak cocok, panggil minta_konfirmasi."
            + (f"\nCatatan pengguna: {note}" if note else "")
        )
        # Anti-dobel semantik: file berbeda tapi toko + tanggal + total sama dengan struk yang sudah tercatat.
        d = res["data"]
        similar = None
        if d.get("total") and ctx.get("default_date"):
            # Nama toko dibandingkan longgar: OCR sering menghilangkan spasi ("SERBAADAMART" vs "SERBA ADA MART").
            cands = db.query("SELECT * FROM transactions WHERE deleted=0 AND source='receipt' AND amount=? AND date=? "
                             "ORDER BY id", (d["total"], ctx["default_date"]))
            similar = next((t for t in cands if same_merchant(t.get("merchant"), d.get("toko"))), None)
        tr.add("dedup", "cek_struk_serupa", {"toko": d.get("toko"), "tanggal": ctx.get("default_date"), "total": d.get("total")},
               {"serupa": bool(similar), "transaksi_serupa": db.tx_row(similar) if similar else None})
        if similar:
            q = (f"Struk ini tampaknya sama dengan transaksi #{similar['id']} yang sudah tercatat "
                 f"({similar['merchant']}, {similar['date']}, Rp{similar['amount']:,}). ".replace(",", ".")
                 + "Apakah ini pembelian yang berbeda dan tetap mau dicatat? Jawab 'ya, catat' atau 'tidak'.")
            raise EarlyExit("awaiting_confirmation", "possible_duplicate", q,
                            [{"role": "user", "content": content},
                             {"role": "assistant", "content": q}])
        return content

    if input_type == "email":
        raw = Path(payload["eml_path"]).read_bytes()
        async with tr.timed("preprocess", "baca_email", {"file": Path(payload["eml_path"]).name,
                                                          "bytes": len(raw), "sumber": payload.get("origin", "upload")}) as box:
            parsed = email_in.parse_eml(raw)
            box["output"] = parsed
        _dedup_or_raise(tr, run_id, email_in.message_key(raw, parsed), "Email yang sama")
        ctx["email"] = {k: parsed[k] for k in ("from", "subject", "date", "message_id")}
        if parsed.get("date") and len(parsed["date"]) >= 10:
            ctx["default_date"] = parsed["date"][:10]
        return (
            "[Email masuk. Ekstrak transaksi bila ada]\n"
            f"Dari: {parsed['from']}\nSubjek: {parsed['subject']}\nTanggal email: {parsed['date']}\n"
            f"Isi:\n{parsed['body']}"
        )

    raise ValueError(f"input_type tidak dikenal: {input_type}")


def _rp(v) -> str:
    return f"Rp{int(v):,}".replace(",", ".")


def fallback_answer(ctx: dict) -> str:
    """Ringkasan deterministik dari hasil tool tulis, dipakai bila LLM tidak menghasilkan teks jawaban."""
    lines = []
    for name, res in ctx.get("write_results", []):
        if name == "catat_transaksi":
            t = res["transaksi"]
            lines.append(f"Tercatat #{t['id']}: {t['kind']} {_rp(t['amount'])} ({t['category']}) - {t.get('description') or ''}, "
                         f"tanggal {t['date']}.")
        elif name == "ubah_transaksi":
            a, b = res["sebelum"], res["sesudah"]
            diff = [f"{k} {_rp(a[k]) if k == 'amount' else a[k]} -> {_rp(b[k]) if k == 'amount' else b[k]}"
                    for k in ("kind", "amount", "category", "description", "merchant", "date") if a.get(k) != b.get(k)]
            lines.append(f"Transaksi #{b['id']} diubah: " + (", ".join(diff) or "tidak ada perubahan") + ".")
        elif name == "hapus_transaksi":
            t = res["dihapus"]
            lines.append(f"Transaksi #{t['id']} dihapus ({t.get('description') or t['category']}, {_rp(t['amount'])}).")
    return " ".join(lines) or "Selesai, tetapi model tidak memberikan jawaban teks. Lihat langkah-langkah trace."


def _outcome(status: str, input_type: str, ctx: dict) -> str:
    """Ringkasan hasil run untuk UI: recorded | modified | answered | not_transaction | needs_confirmation |
    duplicate | possible_duplicate | error."""
    if status == "awaiting_confirmation":
        return "needs_confirmation"
    if ctx.get("created"):
        return "recorded"
    if ctx.get("ok_writes"):
        return "modified"
    if input_type in ("email", "receipt"):
        return "not_transaction"
    return "answered"


async def run_agent(run_id: str) -> None:
    s = get_settings()
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    payload = db.loads(run["input_meta_json"]) or {}
    input_type = run["input_type"]
    tr = Tracer(run_id)
    t_start = time.time()
    db.execute("UPDATE runs SET status='running', started_at=?, model=? WHERE id=?", (t_start, s.llm_model, run_id))
    publish(run_id, "run_status", {"run_id": run_id, "status": "running"})

    totals = {"prompt": 0, "completion": 0, "llm_calls": 0, "tool_calls": 0}
    ctx: dict = {"run_id": run_id, "source": input_type}
    status, answer, error, outcome = "done", None, None, None
    messages: list[dict] = []
    try:
        tr.add("input", input_type, {k: v for k, v in payload.items()}, {"conversation_id": run["conversation_id"]})
        user_content = await _preprocess(run_id, input_type, payload, tr, ctx)
        if "usage_extra" in ctx:
            totals["prompt"] += ctx["usage_extra"][0]
            totals["completion"] += ctx["usage_extra"][1]
            totals["llm_calls"] += ctx["usage_extra"][2]
        db.execute("UPDATE runs SET input_text=? WHERE id=?", (user_content, run_id))

        history, prev = _load_history(run["conversation_id"], run_id)
        if prev and prev["status"] == "awaiting_confirmation" and input_type == "text":
            _inherit_pending(prev, ctx)
            tr.add("context", "lanjutan_konfirmasi", {"run_sebelumnya": prev["id"]},
                   {"sumber_transaksi": ctx["source"], "run_asal": ctx["inherited_from_run"],
                    "tanggal_default": ctx.get("default_date"), "jumlah_item": len(ctx.get("items") or [])})
        messages = [{"role": "system", "content": system_prompt()}, *history, {"role": "user", "content": user_content}]

        for step_no in range(1, s.agent_max_steps + 1):
            res = await llm_step(tr, f"agent_step_{step_no}", messages, tools=tools.TOOL_SPECS)
            msg = res["message"]
            totals["prompt"] += res["prompt_tokens"]
            totals["completion"] += res["completion_tokens"]
            totals["llm_calls"] += 1

            assistant_msg = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                assistant_msg["tool_calls"] = msg["tool_calls"]
            messages.append(assistant_msg)

            calls = msg.get("tool_calls") or []
            if not calls:
                text = (msg.get("content") or "").strip()
                if not ctx.get("nudged") and not ctx.get("ok_tools") and claims_write(text) and step_no < s.agent_max_steps:
                    ctx["nudged"] = True
                    tr.add("guard", "klaim_tanpa_tool", {"jawaban_model": text},
                           {"tindakan": "model diminta memanggil tool", "pesan": NUDGE}, status="warning")
                    messages.append({"role": "user", "content": NUDGE})
                    continue
                if not text:
                    # Dengan thinking aktif, model kadang menutup giliran hanya dengan reasoning tanpa teks jawaban.
                    text = fallback_answer(ctx)
                    tr.add("guard", "jawaban_kosong", {"content": msg.get("content")},
                           {"tindakan": "jawaban dibuat otomatis dari hasil tool", "jawaban": text}, status="warning")
                answer = text
                break

            confirmation = None
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                totals["tool_calls"] += 1
                t0 = time.time()
                try:
                    result = tools.execute_tool(name, args, ctx)
                    st = "ok"
                except (tools.ToolError, KeyError, ValueError, TypeError) as e:
                    result = {"ok": False, "error": str(e),
                              "instruksi": "GAGAL, data BELUM tersimpan. Perbaiki argumen lalu panggil tool lagi, "
                                           "atau sampaikan kegagalan ini ke pengguna. Jangan bilang berhasil."}
                    st = "error"
                    if name in WRITE_TOOLS:
                        ctx.setdefault("failed_writes", []).append(name)
                if st == "ok" and name in WRITE_TOOLS:
                    ctx["ok_writes"] = ctx.get("ok_writes", 0) + 1
                    ctx.setdefault("write_results", []).append((name, result))
                    if name == "catat_transaksi":
                        ctx["created"] = ctx.get("created", 0) + 1
                if st == "ok" and name != "minta_konfirmasi":
                    ctx["ok_tools"] = ctx.get("ok_tools", 0) + 1
                tr.add("tool", name, args, result, status=st, started_at=t0, duration_ms=int((time.time() - t0) * 1000))
                messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False, default=str)})
                if name == "minta_konfirmasi":
                    confirmation = args
            if confirmation:
                status = "awaiting_confirmation"
                answer = confirmation.get("pertanyaan")
                tr.add("confirmation", "minta_konfirmasi", None, confirmation)
                # pertanyaan diteruskan ke pengguna; jawaban datang sebagai pesan baru dgn conversation_id sama
                messages.append({"role": "assistant", "content": answer})
                break
        else:
            answer = answer or "Maaf, batas langkah agent tercapai sebelum selesai."
            status = "done"
        if status == "done" and answer and "?" in answer and not ctx.get("ok_tools") and input_type != "email":
            # Model bertanya lewat teks biasa (bukan tool minta_konfirmasi): tetap perlakukan sebagai menunggu konfirmasi.
            status = "awaiting_confirmation"
            tr.add("confirmation", "pertanyaan_teks", None,
                   {"pertanyaan": answer, "catatan": "terdeteksi dari jawaban teks (heuristik), bukan dari tool minta_konfirmasi"})
        warnings = []
        if ctx.get("failed_writes") and not ctx.get("ok_writes"):
            warnings.append(f"Ada tool tulis yang gagal ({', '.join(ctx['failed_writes'])}) dan tidak ada yang berhasil; "
                            "jawaban agent mungkin tidak akurat.")
            status = "done_with_warnings"
        outcome = _outcome(status, input_type, ctx)
        tr.add("final", status, None, {"answer": answer, "outcome": outcome, "warnings": warnings})
    except EarlyExit as ex:
        status, answer, outcome, messages = ex.status, ex.answer, ex.outcome, ex.messages
        if status == "awaiting_confirmation":
            tr.add("confirmation", "konfirmasi_duplikat", None, {"pertanyaan": answer})
        tr.add("final", status, None, {"answer": answer, "outcome": outcome, "warnings": []})
    except Exception as e:  # noqa: BLE001
        log.exception("run %s gagal", run_id)
        status, error, outcome = "error", err(e), "error"
        tr.add("error", type(e).__name__, None, {"error": err(e)}, status="error")

    t_end = time.time()
    db.execute(
        """UPDATE runs SET status=?, outcome=?, final_answer=?, error=?, prompt_tokens=?, completion_tokens=?, llm_calls=?,
           tool_calls=?, finished_at=?, duration_ms=?, messages_json=? WHERE id=?""",
        (status, outcome, answer, error, totals["prompt"], totals["completion"], totals["llm_calls"], totals["tool_calls"],
         t_end, int((t_end - t_start) * 1000), db.dumps([m for m in messages if m.get("role") != "system"]), run_id),
    )
    publish(run_id, "run_finished", get_run_summary(run_id))


# Kolom turunan (dihitung saat dibaca, tidak disimpan) untuk alur konfirmasi:
# answered_by_run_id = run berikutnya di percakapan yang sama yang menjawab run `awaiting_confirmation` ini;
# answers_run_id     = run `awaiting_confirmation` sebelumnya yang dijawab oleh run ini.
CONFIRM_LINK_COLS = (
    "CASE WHEN runs.status='awaiting_confirmation' THEN (SELECT r2.id FROM runs r2 WHERE "
    "r2.conversation_id=runs.conversation_id AND r2.created_at>runs.created_at ORDER BY r2.created_at LIMIT 1) "
    "END AS answered_by_run_id, "
    "(SELECT r0.id FROM runs r0 WHERE r0.conversation_id=runs.conversation_id AND r0.created_at<runs.created_at "
    "AND r0.status='awaiting_confirmation' AND NOT EXISTS (SELECT 1 FROM runs rm WHERE "
    "rm.conversation_id=runs.conversation_id AND rm.created_at>r0.created_at AND rm.created_at<runs.created_at)) "
    "AS answers_run_id"
)


def get_run_summary(run_id: str) -> dict | None:
    r = db.query_one(f"SELECT runs.*, {CONFIRM_LINK_COLS} FROM runs WHERE id=?", (run_id,))
    if not r:
        return None
    r.pop("messages_json", None)
    r["input_meta"] = db.loads(r.pop("input_meta_json", None))
    r["transactions"] = [db.tx_row(t) for t in db.query("SELECT * FROM transactions WHERE run_id=? ORDER BY id", (run_id,))]
    return r

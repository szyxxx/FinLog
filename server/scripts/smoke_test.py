"""Uji end-to-end terhadap server yang SUDAH berjalan.

    uv run python scripts/smoke_test.py [--base http://localhost:8000] [--key KEY]

Mengirim: teks (via SSE live), foto struk, voice note, 4 email .eml, alur konfirmasi, pertanyaan ringkasan,
cek antrean (2 job bersamaan), lalu memverifikasi transaksi tercatat + trace lengkap + metrik.
Butuh file contoh dari scripts/make_samples.py. Laporan JSON ditulis ke data/smoke_report.json.
"""
import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"

results: list[dict] = []
REASONING_COUNT: list[tuple[str, int, int]] = []
BASELINE_NO_THINK_MS = {"text": 8843, "receipt": 30265, "voice": 11416, "email": 9057}  # smoke test 9 Nov, thinking mati
BASELINE_VISION_NOTHINK_MS = {"receipt": 70270}  # RECEIPT_MODE=vision, VISION_THINK=false, smoke test sebelumnya
BASELINE_THINK_ALL_MS = {"text": 23250, "receipt": 196513, "voice": 29028, "email": 28622}  # thinking aktif termasuk vision


def check(name: str, ok: bool, detail: str = "", hard: bool = True) -> bool:
    results.append({"check": name, "ok": ok, "hard": hard, "detail": detail})
    mark = "PASS" if ok else ("FAIL" if hard else "WARN")
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def read_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("API_KEYS="):
            return line.split("=", 1)[1].split(",")[0].strip()
    raise SystemExit("API key tidak ditemukan di .env")


class Client:
    def __init__(self, base: str, key: str) -> None:
        self.base, self.key = base.rstrip("/"), key
        self.h = httpx.Client(base_url=self.base, headers={"X-API-Key": key}, timeout=900)

    def post_wait(self, path: str, **kw) -> dict:
        t0 = time.time()
        r = self.h.post(path, params={"wait": "true", "timeout": 600}, **kw)
        r.raise_for_status()
        d = r.json()
        d["_client_ms"] = int((time.time() - t0) * 1000)
        return d

    def run(self, run_id: str) -> dict:
        return self.h.get(f"/v1/runs/{run_id}").json()


def verify_trace(c: Client, run_id: str, input_type: str, label: str) -> dict:
    r = c.run(run_id)
    steps = r["steps"]
    types = [s["type"] for s in steps]
    llm_steps = [s for s in steps if s["type"] == "llm"]
    agent_llm = [s for s in llm_steps if (s["name"] or "").startswith("agent_step")]
    tool_steps = [s for s in steps if s["type"] == "tool"]
    by_seq = {s["seq"]: s for s in steps}
    early = r.get("outcome") in ("duplicate", "possible_duplicate")
    ok = (
        types[:1] == ["input"]
        and types[-1] == "final"
        and (bool(agent_llm) or early)
        and all(s["prompt_tokens"] and s["completion_tokens"] is not None and s["duration_ms"] is not None for s in llm_steps)
        and all(isinstance(s["input"].get("messages"), list) and s["input"]["messages"][0]["role"] == "system" for s in agent_llm)
        and all("content" in s["output"] and "tool_calls" in s["output"] for s in llm_steps)
        and all(s["input"] is not None and s["output"] is not None and s["duration_ms"] is not None for s in tool_steps)
        # setiap llm step yang berpikir harus menunjuk step `reasoning` berisi teks thinking
        and all(by_seq.get(s["output"]["reasoning_step_seq"], {}).get("type") == "reasoning"
                and by_seq[s["output"]["reasoning_step_seq"]]["output"]["thinking"]
                for s in llm_steps if s["output"].get("thinking_chars"))
        and r.get("outcome") is not None
    )
    if input_type != "text":
        ok = ok and ("preprocess" in types or "dedup" in types)
    n_reason = types.count("reasoning")
    REASONING_COUNT.append((label, n_reason, len(llm_steps)))
    check(f"{label}: trace lengkap", ok,
          f"{len(steps)} langkah: " + " → ".join(f"{s['type']}:{s['name']}" for s in steps)
          + f" | outcome={r.get('outcome')}, token {r['prompt_tokens']}+{r['completion_tokens']}, {r['duration_ms']} ms")
    return r


def chat_done(c: "Client", text: str, label: str, conv: str | None = None, reply: str = "ya, lanjutkan") -> list[dict]:
    """Kirim chat; bila agent minta konfirmasi, jawab sekali. Kembalikan semua run yang terjadi."""
    d = c.post_wait("/v1/chat", json={"text": text, "conversation_id": conv})
    runs = [verify_trace(c, d["run"]["id"], "text", label)]
    if runs[-1]["status"] == "awaiting_confirmation":
        d = c.post_wait("/v1/chat", json={"text": reply, "conversation_id": runs[-1]["conversation_id"]})
        runs.append(verify_trace(c, d["run"]["id"], "text", label + " → jawaban konfirmasi"))
    return runs


def tx_brief(r: dict) -> str:
    return "; ".join(f"#{t['id']} {t['kind']} Rp{t['amount']:,} {t['category']} {t['date']}".replace(",", ".")
                     for t in r.get("transactions", [])) or "(tidak ada transaksi)"


THINK = False
VISION_THINK = None
EMAIL = True  # /v1/system.email_enabled (fitur email dinonaktifkan pemilik: EMAIL_ENABLED=false)
ENGINE = "custom"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--key", default=None)
    a = ap.parse_args()
    c = Client(a.base, a.key or read_key())
    timings: dict[str, list[int]] = {}

    print("== 0. kesehatan & sistem")
    h = httpx.get(f"{c.base}/health", timeout=10).json()
    check("GET /health", h.get("status") == "ok")
    check("tanpa API key ditolak (401)", httpx.get(f"{c.base}/v1/transactions", timeout=10).status_code == 401)
    sysinfo = c.h.get("/v1/system").json()
    caps = sysinfo["model_capabilities"].get(sysinfo["llm_model"])
    global THINK
    THINK = bool(sysinfo.get("think"))
    global VISION_THINK
    VISION_THINK = sysinfo.get("vision_think")
    global EMAIL, ENGINE
    EMAIL = bool(sysinfo.get("email_enabled", True))
    ENGINE = sysinfo.get("agent_engine") or "custom"
    print(f"  think={THINK}, vision_think={VISION_THINK}, engine={ENGINE}, email_enabled={EMAIL}, "
          f"receipt_mode={sysinfo.get('receipt_mode')}, rules_first={sysinfo.get('receipt_rules_first')}")
    gr = c.h.get("/v1/agent/graph")
    gj = gr.json() if gr.status_code == 200 else {}
    check("GET /v1/agent/graph: engine aktif + diagram Mermaid", gr.status_code == 200 and gj.get("engine") == ENGINE
          and "graph TD" in (gj.get("mermaid") or "") and "agent" in gj.get("nodes", []),
          f"engine={gj.get('engine')}, {len(gj.get('nodes', []))} node, email node: "
          f"{[n for n in gj.get('nodes', []) if 'email' in n]}")
    check("model mendukung tools & vision", isinstance(caps, list) and "tools" in caps and "vision" in caps, f"{sysinfo['llm_model']}: {caps}")

    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Jakarta")).date()
    yesterday = (today - timedelta(days=1)).isoformat()

    # ------------------------------------------------------------ 1. teks + SSE live + antrean
    print("== 1. teks (2 job bersamaan; job pertama di-stream via SSE)")
    j1 = c.h.post("/v1/chat", json={"text": "beli bensin 30rb tadi pagi, terus gajian 4,5 juta"}).json()["job"]
    j2 = c.h.post("/v1/chat", json={"text": "ngopi di warkop 15 ribu"}).json()["job"]
    q = c.h.get("/v1/jobs", params={"limit": 5}).json()
    j2now = next(j for j in q["jobs"] if j["id"] == j2["id"])
    check("antrean: job ke-2 menunggu di belakang job ke-1", j2now["status"] == "queued" and (j2now["queue_position"] or 0) >= 1,
          f"queue_length={q['queue_length']}, job2 status={j2now['status']} posisi={j2now['queue_position']}")

    events: list[tuple[float, str, dict]] = []
    t_sse = time.time()
    with httpx.stream("GET", f"{c.base}/v1/runs/{j1['run_id']}/stream", params={"api_key": c.key}, timeout=600) as resp:
        ev = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and ev:
                data = json.loads(line.split(":", 1)[1])
                events.append((round(time.time() - t_sse, 2), ev, data))
                if ev == "run_finished":
                    break
    step_events = [e for e in events if e[1] == "step"]
    deltas = [e for e in events if e[1] == "llm_delta"]
    think_deltas = [e for e in deltas if e[2]["kind"] == "thinking"]
    reasoning_ev = [e for e in step_events if e[2]["type"] == "reasoning"]
    if THINK:
        check("SSE: potongan thinking di-stream live (llm_delta) + step reasoning", bool(think_deltas) and bool(reasoning_ev),
              f"{len(think_deltas)} event llm_delta thinking (pertama di detik {think_deltas[0][0] if think_deltas else '-'}), "
              f"{len(reasoning_ev)} step reasoning; contoh: {(think_deltas[0][2]['text'][:80] if think_deltas else '')!r}")
    events = [e for e in events if e[1] != "llm_delta"]  # ringkas untuk laporan
    check("SSE: menerima langkah live sampai run_finished",
          bool(step_events) and events[-1][1] == "run_finished",
          f"{len(events)} event; waktu tiba (s): " + ", ".join(f"{t}:{e}{('/' + d.get('type', '')) if e == 'step' else ''}" for t, e, d in events[:14]))
    r1 = verify_trace(c, j1["run_id"], "text", "teks")
    kinds = sorted((t["kind"], t["amount"]) for t in r1["transactions"])
    check("teks: bensin 30.000 & gaji 4.500.000 tercatat", ("pemasukan", 4500000) in kinds and ("pengeluaran", 30000) in kinds, tx_brief(r1))
    timings.setdefault("text", []).append(r1["duration_ms"])
    # job 2 selesai setelah job 1
    while c.h.get(f"/v1/jobs/{j2['id']}").json()["status"] in ("queued", "running"):
        time.sleep(1)
    j2d = c.h.get(f"/v1/jobs/{j2['id']}").json()
    r2 = c.run(j2["run_id"])
    check("antrean: job ke-2 diproses setelah job ke-1 (berurutan)", r2["started_at"] >= r1["finished_at"] - 0.01,
          f"tunggu antrean {j2d['wait_ms']} ms; {tx_brief(r2)}")
    timings["text"].append(r2["duration_ms"])

    # ------------------------------------------------------------ 2. struk
    print("== 2. foto struk")
    with open(SAMPLES / "struk_serba_ada.png", "rb") as f:
        d = c.post_wait("/v1/receipt", files={"file": ("struk.png", f, "image/png")})
    rr = verify_trace(c, d["run"]["id"], "receipt", "struk")
    ext = next(s for s in rr["steps"] if s["name"] == "ekstrak_struk")["output"]["data"]
    check("struk: JSON terstruktur (toko, item, total)", ext.get("total") == 47700 and len(ext.get("items") or []) == 4,
          f"toko={ext.get('toko')}, tanggal={ext.get('tanggal')}, total={ext.get('total')}, {len(ext.get('items') or [])} item, "
          f"keyakinan={ext.get('keyakinan')}, konsistensi={ext.get('cek_konsistensi')}")
    prep = next(s for s in rr["steps"] if s["name"] == "ekstrak_struk")
    mode = prep["input"].get("receipt_mode")
    ocr_step = next((s for s in rr["steps"] if s["type"] == "ocr"), None)
    val_step = next((s for s in rr["steps"] if s["type"] == "validation"), None)
    fb_step = next((s for s in rr["steps"] if s["type"] == "fallback"), None)
    if mode in ("ocr_llm", "ocr_only"):
        lines = (ocr_step or {}).get("output", {}).get("lines") or []
        check(f"struk ({mode}): step ocr berisi baris + bbox + confidence",
              bool(lines) and all(len(ln["bbox"]) == 4 and "conf" in ln for ln in lines)
              and bool(ocr_step["output"].get("boxes")) and ocr_step["duration_ms"] is not None,
              f"{len(lines)} baris, {ocr_step['output'].get('n_boxes') if ocr_step else 0} kotak, mean_conf="
              f"{(ocr_step or {}).get('output', {}).get('mean_conf')}, {(ocr_step or {}).get('duration_ms')} ms, "
              f"gambar {(ocr_step or {}).get('output', {}).get('image_size')}")
        check(f"struk ({mode}): step validasi ada", val_step is not None,
              f"lolos={(val_step or {}).get('output', {}).get('lolos')}, gagal={(val_step or {}).get('output', {}).get('gagal')}")
        check(f"struk ({mode}): sumber ekstraksi", True,
              f"sumber={ext.get('sumber_ekstraksi')}, fallback={'ya: ' + str(fb_step['input']['alasan']) if fb_step else 'tidak'}, "
              f"jalur={prep['output'].get('jalur')}", hard=False)
        if sysinfo.get("receipt_rules_first") and mode == "ocr_llm":
            rules_val = next((s for s in rr["steps"] if s["type"] == "validation" and s["input"].get("sumber") == "rules_first"), None)
            llm_ext = [s["name"] for s in rr["steps"] if s["type"] == "llm" and s["name"].startswith("ekstrak_struk")]
            lolos = bool(rules_val and rules_val["output"].get("lolos"))
            check("struk (rules-first): aturan dicoba dulu; lolos → LLM ekstraksi dilewati, gagal → LLM teks/vision",
                  rules_val is not None and ((lolos and not llm_ext and ext.get("sumber_ekstraksi") == "ocr_rules")
                                             or (not lolos and bool(llm_ext))),
                  f"validasi aturan lolos={lolos}, LLM ekstraksi={llm_ext or 'dilewati'}, sumber={ext.get('sumber_ekstraksi')}, "
                  f"durasi ekstraksi {prep['duration_ms']} ms")
    img_bytes = (SAMPLES / "struk_serba_ada.png").read_bytes()
    gi = c.h.get(f"/v1/runs/{rr['id']}/image")
    gk = httpx.get(f"{c.base}/v1/runs/{rr['id']}/image", params={"key": c.key}, timeout=30)
    gn = httpx.get(f"{c.base}/v1/runs/{rr['id']}/image", timeout=30)
    g404 = c.h.get(f"/v1/runs/{r1['id']}/image")
    check("GET /v1/runs/{id}/image: file asli (header & ?key=), 401 tanpa key, 404 untuk run teks",
          gi.status_code == 200 and gi.content == img_bytes and gk.status_code == 200 and gk.content == img_bytes
          and gn.status_code == 401 and g404.status_code == 404,
          f"header={gi.status_code} {gi.headers.get('content-type')} {len(gi.content)} B (sama={gi.content == img_bytes}), "
          f"?key={gk.status_code}, tanpa key={gn.status_code}, run teks={g404.status_code}")
    txs = rr["transactions"]
    check("struk: transaksi Rp47.700 tercatat", any(t["amount"] == 47700 and t["kind"] == "pengeluaran" for t in txs), tx_brief(rr))
    check("struk: tanggal = tanggal struk (kemarin)", any(t["date"] == yesterday for t in txs), tx_brief(rr), hard=False)
    check("struk: item tersimpan di transaksi", any(t.get("items") for t in txs), hard=False)
    timings.setdefault("receipt", []).append(rr["duration_ms"])

    print("== 2b. anti-dobel struk")
    n_before = c.h.get("/v1/transactions", params={"periode": "semua"}).json()["jumlah_hasil"]
    with open(SAMPLES / "struk_serba_ada.png", "rb") as f:
        d = c.post_wait("/v1/receipt", files={"file": ("struk-lagi.png", f, "image/png")})
    rd = verify_trace(c, d["run"]["id"], "receipt", "struk diunggah ulang (file sama)")
    check("struk file sama: ditolak sebagai duplikat tanpa memanggil LLM",
          rd["status"] == "done" and rd["outcome"] == "duplicate" and rd["llm_calls"] == 0 and not rd["transactions"],
          f"status={rd['status']}, outcome={rd['outcome']}, {rd['duration_ms']} ms, jawaban: {rd['final_answer']}")
    with open(SAMPLES / "struk_serba_ada_foto2.jpg", "rb") as f:
        d = c.post_wait("/v1/receipt", files={"file": ("foto2.jpg", f, "image/jpeg")})
    rp = verify_trace(c, d["run"]["id"], "receipt", "foto lain dari struk yang sama")
    check("struk serupa (toko+tanggal+total sama): diminta konfirmasi",
          rp["status"] == "awaiting_confirmation" and rp["outcome"] == "possible_duplicate",
          f"status={rp['status']}, outcome={rp['outcome']}, pertanyaan: {rp['final_answer']}")
    if rp["status"] == "awaiting_confirmation":
        d = c.post_wait("/v1/chat", json={"text": "tidak, itu struk yang sama, jangan dicatat", "conversation_id": rp["conversation_id"]})
        rn = verify_trace(c, d["run"]["id"], "text", "jawaban 'tidak' untuk struk serupa")
        check("struk serupa: setelah dijawab 'tidak', tidak ada transaksi baru", not rn["transactions"], f"jawaban: {rn['final_answer']}")
    n_after = c.h.get("/v1/transactions", params={"periode": "semua"}).json()["jumlah_hasil"]
    check("anti-dobel struk: jumlah transaksi tidak bertambah", n_after == n_before, f"{n_before} → {n_after}")

    # ------------------------------------------------------------ 3. voice note
    print("== 3. voice note")
    with open(SAMPLES / "voice_bakso.wav", "rb") as f:
        d = c.post_wait("/v1/voice", files={"file": ("voice.wav", f, "audio/wav")})
    rv = verify_trace(c, d["run"]["id"], "voice", "voice")
    stt = next(s for s in rv["steps"] if s["name"] == "transkripsi_suara")["output"]
    check("voice: transkripsi", bool(stt.get("text")),
          f"\"{stt.get('text')}\" ({stt.get('audio_seconds')} s audio, {stt.get('duration_ms')} ms, engine={stt.get('engine')})")
    timings.setdefault("voice", []).append(rv["duration_ms"])
    ga = c.h.get(f"/v1/runs/{rv['id']}/audio")
    gak = httpx.get(f"{c.base}/v1/runs/{rv['id']}/audio", params={"api_key": c.key}, timeout=30)
    wav = (SAMPLES / "voice_bakso.wav").read_bytes()
    check("GET /v1/runs/{id}/audio: voice note asli", ga.status_code == 200 and ga.content == wav and gak.status_code == 200
          and c.h.get(f"/v1/runs/{rv['id']}/image").status_code == 404,
          f"{ga.status_code} {ga.headers.get('content-type')} {len(ga.content)} B (sama={ga.content == wav}), ?api_key={gak.status_code}")
    vtx = list(rv["transactions"])
    if rv["status"] == "awaiting_confirmation":
        # Audio TTS robot sering salah dengar ("bakso" → "makso"); agent wajar bertanya. Jawab seperti pengguna.
        check("voice: agent minta konfirmasi atas transkrip yang meragukan", True, f"pertanyaan: {rv['final_answer']}", hard=False)
        d2 = c.post_wait("/v1/chat", json={"text": "iya benar, maksudnya makan bakso 25 ribu", "conversation_id": rv["conversation_id"]})
        rv2 = verify_trace(c, d2["run"]["id"], "text", "voice → jawaban konfirmasi")
        vtx += rv2["transactions"]
    check("voice: transaksi Rp25.000 tercatat", any(t["amount"] == 25000 for t in vtx),
          "; ".join(f"#{t['id']} {t['kind']} Rp{t['amount']} {t['category']} via {t['source']}" for t in vtx) or "(tidak ada)")

    # ------------------------------------------------------------ 4. email
    if not EMAIL:
        print("== 4. email: fitur dinonaktifkan (EMAIL_ENABLED=false), kasus email dilewati")
        n_jobs = len(c.h.get("/v1/jobs", params={"limit": 500}).json()["jobs"])
        with open(SAMPLES / "email_transfer_masuk.eml", "rb") as f:
            e1 = c.h.post("/v1/email", files={"file": ("x.eml", f, "message/rfc822")})
        e2 = c.h.post("/v1/email/imap/fetch")
        n_jobs2 = len(c.h.get("/v1/jobs", params={"limit": 500}).json()["jobs"])
        check("email nonaktif: /v1/email & /v1/email/imap/fetch → 410 dengan pesan jelas, tanpa membuat job",
              e1.status_code == 410 and e2.status_code == 410 and n_jobs2 == n_jobs and "email" in e1.json().get("detail", "").lower(),
              f"/v1/email={e1.status_code} ({e1.json().get('detail')!r}), imap={e2.status_code}, job {n_jobs}→{n_jobs2}")
    print("== 4. email .eml" if EMAIL else "")
    expect = {
        "email_transfer_masuk": ("pemasukan", 750000),
        "email_ewallet_bayar": ("pengeluaran", 42000),
        "email_invoice_belanja": ("pengeluaran", 177000),
        "email_promo": None,
    }
    for name, exp in (expect.items() if EMAIL else ()):
        with open(SAMPLES / f"{name}.eml", "rb") as f:
            d = c.post_wait("/v1/email", files={"file": (f"{name}.eml", f, "message/rfc822")})
        re_ = verify_trace(c, d["run"]["id"], "email", name)
        got = [(t["kind"], t["amount"]) for t in re_["transactions"]]
        if exp:
            check(f"{name}: {exp[0]} Rp{exp[1]:,} tercatat".replace(",", "."), exp in got, tx_brief(re_))
        else:
            check(f"{name}: email promo tidak dicatat, status done + outcome not_transaction",
                  not got and re_["status"] == "done" and re_["outcome"] == "not_transaction",
                  f"status={re_['status']}, outcome={re_['outcome']}, jawaban: {(re_['final_answer'] or '')[:120]}")
        timings.setdefault("email", []).append(re_["duration_ms"])

    if EMAIL:
        print("== 4b. anti-dobel email (Message-ID)")
        n_before = c.h.get("/v1/transactions", params={"periode": "semua"}).json()["jumlah_hasil"]
        with open(SAMPLES / "email_transfer_masuk.eml", "rb") as f:
            d = c.post_wait("/v1/email", files={"file": ("salinan.eml", f, "message/rfc822")})
        rde = verify_trace(c, d["run"]["id"], "email", "email diunggah ulang")
        dd = next((s for s in rde["steps"] if s["type"] == "dedup"), {"input": {}})
        n_after = c.h.get("/v1/transactions", params={"periode": "semua"}).json()["jumlah_hasil"]
        check("email sama: tidak dicatat dua kali", rde["outcome"] == "duplicate" and rde["llm_calls"] == 0 and n_after == n_before,
              f"kunci={dd['input'].get('kunci')}, outcome={rde['outcome']}, jawaban: {rde['final_answer']}")

    # ------------------------------------------------------------ 4c. ubah & hapus
    print("== 4c. ubah & hapus transaksi (REST dan agent)")
    t = c.h.post("/v1/transactions", json={"jenis": "pengeluaran", "jumlah": 12000, "kategori": "transportasi",
                                             "deskripsi": "parkir mall (uji REST)"})
    tx = t.json()
    p = c.h.patch(f"/v1/transactions/{tx['id']}", json={"jumlah": 15000, "kategori": "hiburan"})
    g = c.h.get(f"/v1/transactions/{tx['id']}").json()
    check("REST: POST lalu PATCH transaksi", t.status_code == 201 and p.status_code == 200 and g["amount"] == 15000 and g["category"] == "hiburan",
          f"#{tx['id']} 12000/transportasi → {g['amount']}/{g['category']}")
    dl = c.h.delete(f"/v1/transactions/{tx['id']}")
    g404 = c.h.get(f"/v1/transactions/{tx['id']}")
    check("REST: DELETE transaksi lalu GET → 404", dl.status_code == 200 and g404.status_code == 404,
          f"DELETE {dl.status_code}, GET {g404.status_code}")
    check("REST: PATCH id tidak ada → 404", c.h.patch("/v1/transactions/999999", json={"jumlah": 1}).status_code == 404)

    bensin = next(t for t in r1["transactions"] if t["amount"] == 30000)
    runs = chat_done(c, "ralat ya, beli bensin tadi pagi ternyata 35 ribu bukan 30 ribu", "agent ubah")
    used = [s["name"] for r in runs for s in r["steps"] if s["type"] == "tool"]
    g = c.h.get(f"/v1/transactions/{bensin['id']}").json()
    check("agent: ubah nominal bensin 30.000 → 35.000 (ubah_transaksi)", g.get("amount") == 35000 and "ubah_transaksi" in used,
          f"tools={used}; #{bensin['id']} sekarang Rp{g.get('amount')}; jawaban: {runs[-1]['final_answer']}")
    timings.setdefault("text_ubah_hapus", []).extend(r["duration_ms"] for r in runs)
    ngopi = r2["transactions"][0] if r2["transactions"] else None
    runs = chat_done(c, "hapus catatan ngopi di warkop 15 ribu tadi, salah input", "agent hapus", reply="ya, hapus")
    used = [s["name"] for r in runs for s in r["steps"] if s["type"] == "tool"]
    gone = ngopi and c.h.get(f"/v1/transactions/{ngopi['id']}").status_code == 404
    check("agent: hapus transaksi ngopi (hapus_transaksi)", bool(gone) and "hapus_transaksi" in used,
          f"tools={used}; #{ngopi['id'] if ngopi else '?'} terhapus={gone}; jawaban: {runs[-1]['final_answer']}")
    timings.setdefault("text_ubah_hapus", []).extend(r["duration_ms"] for r in runs)

    # ------------------------------------------------------------ 5. konfirmasi (ambigu)
    print("== 5. alur konfirmasi untuk input ambigu")
    d = c.post_wait("/v1/chat", json={"text": "tadi bayar 50"})
    ra = verify_trace(c, d["run"]["id"], "text", "ambigu")
    asked = ra["status"] == "awaiting_confirmation"
    check("ambigu: agent memanggil minta_konfirmasi", asked, f"status={ra['status']}, jawaban: {ra['final_answer']}", hard=False)
    if asked:
        d2 = c.post_wait("/v1/chat", json={"text": "maksudnya 50 ribu, bayar parkir motor bulanan",
                                            "conversation_id": ra["conversation_id"]})
        rb = verify_trace(c, d2["run"]["id"], "text", "jawaban konfirmasi")
        check("konfirmasi: transaksi Rp50.000 tercatat setelah dijawab", any(t["amount"] == 50000 for t in rb["transactions"]), tx_brief(rb))
        ra2 = c.run(ra["id"])
        lst = c.h.get("/v1/runs", params={"conversation_id": ra["conversation_id"]}).json()
        la = next(x for x in lst["runs"] if x["id"] == ra["id"])
        check("answered_by_run_id / answers_run_id menghubungkan pertanyaan & jawaban (status lama tetap)",
              ra2["answered_by_run_id"] == rb["id"] and rb["answers_run_id"] == ra["id"] and la["answered_by_run_id"] == rb["id"]
              and ra2["status"] == "awaiting_confirmation" and rb["answered_by_run_id"] is None,
              f"{ra['id']}.answered_by_run_id={ra2['answered_by_run_id']}, {rb['id']}.answers_run_id={rb['answers_run_id']}, "
              f"status run pertanyaan={ra2['status']}")
    timings["text"].append(ra["duration_ms"])

    # ------------------------------------------------------------ 6. pertanyaan
    print("== 6. pertanyaan / ringkasan")
    d = c.post_wait("/v1/chat", json={"text": "total pengeluaran bulan ini berapa? kategori apa yang paling besar?"})
    rq = verify_trace(c, d["run"]["id"], "text", "pertanyaan")
    used = [s["name"] for s in rq["steps"] if s["type"] == "tool"]
    summ = c.h.get("/v1/summary", params={"periode": "bulan_ini"}).json()
    check("pertanyaan: agent memakai tool ringkasan/cari", any(u in ("ringkasan", "cari_transaksi") for u in used),
          f"tools={used}; jawaban: {rq['final_answer']}")
    total_str = f"{summ['total_pengeluaran']:,}".replace(",", ".")
    check("pertanyaan: jawaban memuat total pengeluaran yang benar", total_str in (rq["final_answer"] or ""),
          f"total_pengeluaran REST={total_str}", hard=False)
    timings["text"].append(rq["duration_ms"])

    # ------------------------------------------------------------ 6b. validasi input & celah dari uji live web app
    print("== 6b. validasi input (temuan uji live web app)")
    before = c.h.get(f"/v1/transactions/{bensin['id']}").json()
    p_gaji = c.h.patch(f"/v1/transactions/{bensin['id']}", json={"kategori": "gaji"})
    p_unk = c.h.patch(f"/v1/transactions/{bensin['id']}", json={"kategori": "jajan_bebas"})
    p_kind = c.h.patch(f"/v1/transactions/{bensin['id']}", json={"jenis": "pemasukan"})
    after = c.h.get(f"/v1/transactions/{bensin['id']}").json()
    p_ok = c.h.patch(f"/v1/transactions/{bensin['id']}", json={"jenis": "pemasukan", "kategori": "lainnya_masuk"})
    c.h.patch(f"/v1/transactions/{bensin['id']}", json={"jenis": before["kind"], "kategori": before["category"]})  # kembalikan
    check("PATCH kategori tak dikenal / tak cocok jenis → 400, data tidak berubah",
          p_gaji.status_code == 400 and p_unk.status_code == 400 and p_kind.status_code == 400 and p_ok.status_code == 200
          and (after["kind"], after["category"], after["amount"]) == (before["kind"], before["category"], before["amount"]),
          f"gaji→{p_gaji.status_code} ({p_gaji.json().get('detail', '')[:70]}), tak dikenal→{p_unk.status_code}, "
          f"jenis saja→{p_kind.status_code}, jenis+kategori cocok→{p_ok.status_code}")

    n_jobs = len(c.h.get("/v1/jobs", params={"limit": 500}).json()["jobs"])
    b1 = c.h.post("/v1/chat", json={"text": "   "})
    b2 = c.h.post("/v1/chat", json={"text": "\n\t "})
    n_jobs2 = len(c.h.get("/v1/jobs", params={"limit": 500}).json()["jobs"])
    check("POST /v1/chat teks kosong/spasi → 422 tanpa membuat job", b1.status_code == 422 and b2.status_code == 422 and n_jobs2 == n_jobs,
          f"{b1.status_code}/{b2.status_code}, job {n_jobs}→{n_jobs2}")

    q1 = c.h.get("/v1/transactions", params={"periode": "kemarinlusa"})
    q2 = c.h.get("/v1/summary", params={"periode": "bulan_depan"})
    q3 = c.h.get("/v1/transactions", params={"periode": "bulan_ini"})
    check("periode tidak valid → 400 (transaksi & summary)", q1.status_code == 400 and q2.status_code == 400 and q3.status_code == 200,
          f"transactions={q1.status_code} ({q1.json().get('detail', '')[:60]}), summary={q2.status_code}, valid={q3.status_code}")

    root_s = str(ROOT)
    bad = c.h.post("/v1/receipt", files={"file": ("bukan-gambar.png", b"ini bukan gambar", "image/png")})
    dv = c.post_wait("/v1/voice", files={"file": ("rusak.ogg", b"ini bukan audio sama sekali", "audio/ogg")})
    rv_bad = c.run(dv["run"]["id"])
    err_texts = [bad.text, str(rv_bad.get("error")), str(dv["job"].get("error"))] + \
                [json.dumps(st["output"]) for st in rv_bad["steps"] if st["status"] == "error"]
    leak = [t[:120] for t in err_texts if root_s in t or "/home/" in t]
    check("error unggahan tidak membocorkan path absolut server",
          bad.status_code == 400 and rv_bad["status"] == "error" and not leak,
          f"receipt 400: {bad.json().get('detail')!r}; voice rusak: status={rv_bad['status']}, error={str(rv_bad.get('error'))[:110]!r}"
          + (f"; BOCOR: {leak}" if leak else ""))

    lr = c.h.get("/v1/runs", params={"limit": 200}).json()["runs"]
    rec = next(x for x in lr if x["input_type"] == "receipt")
    em = next((x for x in lr if x["input_type"] == "email" and x["outcome"] != "duplicate"), None)
    tx_ = next(x for x in lr if x["input_type"] == "text")
    check("GET /v1/runs: item punya input_meta.filename (+ subject untuk email), tanpa path",
          rec["input_meta"]["filename"] and (not EMAIL or (em["input_meta"].get("filename") and em["input_meta"].get("subject")))
          and tx_["input_meta"] == {"filename": None} and all("image_path" not in x["input_meta"] for x in lr),
          f"receipt={rec['input_meta']}, email={em['input_meta'] if em else '(email nonaktif)'}, text={tx_['input_meta']}")

    if ENGINE == "langgraph":
        lg = [x for x in lr if x["input_type"] in ("text", "receipt", "voice")]
        missing, engines = [], set()
        for x in lg[:60]:
            full = c.run(x["id"])
            engines.add(full.get("engine"))
            missing += [f"{x['id']}#{st['seq']}:{st['type']}" for st in full["steps"] if st["type"] != "error" and not st.get("node")]
        check("langgraph: setiap step punya field node, run.engine = langgraph", not missing and engines == {"langgraph"},
              f"{len(lg[:60])} run diperiksa, engine={engines}, step tanpa node={missing[:5]}")

    # ------------------------------------------------------------ 7. REST transaksi & metrik
    print("== 7. REST & metrik")
    txl = c.h.get("/v1/transactions", params={"periode": "semua", "limit": 100}).json()
    check("GET /v1/transactions", txl["jumlah_hasil"] >= (7 if EMAIL else 4), f"{txl['jumlah_hasil']} transaksi, total nominal {txl['total_nominal']:,}".replace(",", "."))
    m = c.h.get("/v1/metrics").json()
    rbs = m["runs_by_status"]
    check("metrics.runs_by_status: awaiting_confirmation_answered (field lama tetap)",
          "awaiting_confirmation" in rbs and 1 <= rbs.get("awaiting_confirmation_answered", 0) <= rbs["awaiting_confirmation"],
          f"{rbs}")
    g = (m["gpu"].get("gpus") or [{}])[0]
    check("GET /v1/metrics (GPU/VRAM, token/s, antrean)", m["gpu"].get("available") and m["llm"]["tokens_per_second_avg"] is not None,
          f"VRAM {g.get('memory_used_mib')}/{g.get('memory_total_mib')} MiB, tok/s avg {m['llm']['tokens_per_second_avg']}, "
          f"antrean {m['queue']['length']}, whisper={m['stt'].get('device')}")

    hard_fail = [r for r in results if not r["ok"] and r["hard"]]
    soft_fail = [r for r in results if not r["ok"] and not r["hard"]]
    print(f"\n== waktu respons (durasi run di server, ms; think={THINK}; pembanding = smoke test thinking mati)")
    for k, v in timings.items():
        base = BASELINE_NO_THINK_MS.get(k)
        avg = sum(v) // len(v)
        print(f"  {k:8s} n={len(v)}  min={min(v)}  max={max(v)}  rata2={avg}"
              + (f"  (thinking mati: {base}, x{avg / base:.1f}" if base else "")
              + (f"; thinking semua termasuk vision: {BASELINE_THINK_ALL_MS[k]})" if base else ""))
    if timings.get("receipt"):
        print(f"  struk sebelumnya (mode vision tanpa thinking): {BASELINE_VISION_NOTHINK_MS['receipt']} ms")
    n_runs_reason = sum(1 for _, n, _ in REASONING_COUNT if n)
    print(f"  step reasoning: {sum(n for _, n, _ in REASONING_COUNT)} dari {sum(m for _, _, m in REASONING_COUNT)} panggilan LLM "
          f"({n_runs_reason}/{len(REASONING_COUNT)} run punya reasoning)")
    print(f"\nHASIL: {len(results) - len(hard_fail) - len(soft_fail)} PASS, {len(soft_fail)} WARN, {len(hard_fail)} FAIL")
    report = {"results": results, "engine": ENGINE, "email_enabled": EMAIL, "think": THINK, "vision_think": VISION_THINK, "reasoning_count": REASONING_COUNT, "timings_ms": timings, "metrics": m, "sse_events": [(t, e) for t, e, _ in events]}
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "smoke_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())

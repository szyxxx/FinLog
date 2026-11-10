"""FastAPI AI server: agent pencatat keuangan (teks, struk, voice note, email) + trace + antrean + metrik."""
import asyncio
import json
import logging
import mimetypes
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader, APIKeyQuery
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from . import db, engine, llm, metrics, tools
from .agent import CONFIRM_LINK_COLS, get_run_summary
from .config import get_settings
from .inputs import email_in
from .jobqueue import job_queue
from .util import err
from .trace import bus, step_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server")
settings = get_settings()

MAX_IMAGE = 10 * 1024 * 1024
MAX_AUDIO = 25 * 1024 * 1024
MAX_EML = 5 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    await engine.startup()
    job_queue.start()
    log.info("server siap di %s:%s, model=%s, engine=%s", settings.host, settings.port, settings.llm_model,
             settings.agent_engine)
    yield
    await job_queue.stop()
    await engine.shutdown()


app = FastAPI(
    title="AI Finance Agent Server",
    version="0.1.0",
    description="Prototipe local AI server (Infrastruktur & Platform Cerdas). Autentikasi: header `X-API-Key` "
                "(atau query `api_key` untuk EventSource/SSE). Lihat docs/api.md.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_hdr = APIKeyHeader(name="X-API-Key", auto_error=False)
_qry = APIKeyQuery(name="api_key", auto_error=False)
_qry2 = APIKeyQuery(name="key", auto_error=False)  # alias pendek, praktis untuk <img src="...?key=">
_hits: dict[str, deque] = defaultdict(deque)


def require_key(h: str | None = Depends(_hdr), q: str | None = Depends(_qry), q2: str | None = Depends(_qry2)) -> str:
    key = h or q or q2
    if not key or key not in settings.api_key_list:
        raise HTTPException(401, "API key tidak valid / tidak ada (header X-API-Key)")
    return key


def require_email_enabled(key: str = Depends(require_key)) -> str:
    if not settings.email_enabled:
        raise HTTPException(410, "Fitur email dinonaktifkan di server ini (EMAIL_ENABLED=false). Catat transaksi lewat "
                                 "teks, foto struk, atau voice note.")
    return key


def rate_limit(key: str = Depends(require_key)) -> str:
    now = time.time()
    dq = _hits[key]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= settings.rate_limit_per_minute:
        raise HTTPException(429, f"rate limit {settings.rate_limit_per_minute} job/menit terlampaui")
    dq.append(now)
    return key


# ------------------------------------------------------------------ schemas

class ChatIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000, examples=["beli bensin 30rb tadi pagi"])
    conversation_id: str | None = Field(None, description="Isi untuk melanjutkan percakapan (mis. menjawab minta_konfirmasi)")

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("teks tidak boleh kosong atau hanya spasi")
        return v


class TxIn(BaseModel):
    jenis: Literal["pemasukan", "pengeluaran"]
    jumlah: int = Field(..., gt=0)
    kategori: str = "lainnya_keluar"
    deskripsi: str | None = None
    merchant: str | None = None
    tanggal: str | None = Field(None, description="YYYY-MM-DD, default hari ini")


class TxPatch(BaseModel):
    jenis: Literal["pemasukan", "pengeluaran"] | None = None
    jumlah: int | None = Field(None, gt=0)
    kategori: str | None = None
    deskripsi: str | None = None
    merchant: str | None = None
    tanggal: str | None = None


async def _respond(job: dict, wait: bool, timeout: float) -> dict:
    if wait:
        finished = await job_queue.wait(job["id"], timeout)
        job = job_queue.job_info(job["id"])
        return {"job": job, "run": get_run_summary(job["run_id"]), "timed_out": not finished}
    return {"job": job, "run": None, "timed_out": False}


async def _save_upload(f: UploadFile, limit: int, kind: str) -> tuple[Path, int]:
    data = await f.read()
    if not data:
        raise HTTPException(400, "file kosong")
    if len(data) > limit:
        raise HTTPException(413, f"file terlalu besar (maks {limit // 1024 // 1024} MB)")
    suffix = Path(f.filename or "").suffix.lower()[:8] or ".bin"
    path = Path(settings.upload_dir) / f"{kind}_{db.new_id('f')}{suffix}"
    path.write_bytes(data)
    return path, len(data)


WaitQ = Query(False, description="true = tunggu sampai run selesai lalu kembalikan hasil (praktis untuk curl)")
TimeoutQ = Query(600, ge=1, le=900, description="Batas tunggu (detik) bila wait=true")


# ------------------------------------------------------------------ public

@app.get("/health", tags=["sistem"])
async def health():
    return {"status": "ok", "time": db.iso_now(), "queue_length": job_queue.length}


@app.get("/v1/system", tags=["sistem"], dependencies=[Depends(require_key)])
async def system_info():
    caps = {}
    for m in {settings.llm_model, settings.vision_model}:
        try:
            caps[m] = (await llm.show(m)).get("capabilities")
        except Exception as e:  # noqa: BLE001
            caps[m] = f"error: {e!r}"
    return {
        "agent_engine": settings.agent_engine,
        "llm_model": settings.llm_model, "vision_model": settings.vision_model, "think": settings.llm_think, "vision_think": settings.vision_think, "receipt_mode": settings.receipt_mode,
        "receipt_fallback_vision": settings.receipt_fallback_vision, "receipt_rules_first": settings.receipt_rules_first,
        "model_capabilities": caps, "whisper_model": settings.whisper_model, "whisper_device": settings.whisper_device,
        "email_enabled": settings.email_enabled, "imap_enabled": settings.email_enabled and settings.imap_enabled,
        "tools": tools.TOOL_SPECS, "categories": [
            {"name": n, "kind": k, "description": d} for n, k, d in db.CATEGORIES],
    }


@app.get("/v1/agent/graph", tags=["sistem"], dependencies=[Depends(require_key)])
async def agent_graph():
    """Diagram Mermaid graf LangGraph (dibuat lokal) + mesin agent yang aktif (AGENT_ENGINE)."""
    try:
        from .graph_agent import build_graph, mermaid
        g = build_graph().get_graph()
        nodes = [n for n in g.nodes if not n.startswith("__")]
        edges = [{"source": e.source, "target": e.target, "conditional": e.conditional} for e in g.edges]
        return {"engine": settings.agent_engine, "langgraph_available": True, "mermaid": mermaid(),
                "nodes": nodes, "edges": edges}
    except ImportError as e:
        return {"engine": settings.agent_engine, "langgraph_available": False, "mermaid": None, "nodes": [], "edges": [],
                "error": err(e)}


# ------------------------------------------------------------------ input (membuat job)

@app.post("/v1/chat", tags=["input"], dependencies=[Depends(rate_limit)])
async def chat(body: ChatIn, wait: bool = WaitQ, timeout: float = TimeoutQ):
    """Input teks / chat: pencatatan, pertanyaan, ubah/hapus, atau menjawab konfirmasi."""
    job = job_queue.submit("text", {"text": body.text}, body.conversation_id)
    return await _respond(job, wait, timeout)


@app.post("/v1/receipt", tags=["input"], dependencies=[Depends(rate_limit)])
async def upload_receipt(file: UploadFile = File(..., description="Foto/scan struk (jpg/png/webp)"),
                         note: str | None = Form(None), conversation_id: str | None = Form(None),
                         wait: bool = WaitQ, timeout: float = TimeoutQ):
    path, size = await _save_upload(file, MAX_IMAGE, "receipt")
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
    except Exception as e:  # noqa: BLE001
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"bukan gambar yang valid ({type(e).__name__}); unggah JPG/PNG/WEBP") from e
    job = job_queue.submit("receipt", {"image_path": str(path), "filename": file.filename, "bytes": size, "note": note},
                           conversation_id)
    return await _respond(job, wait, timeout)


@app.post("/v1/voice", tags=["input"], dependencies=[Depends(rate_limit)])
async def upload_voice(file: UploadFile = File(..., description="Voice note (ogg/opus, m4a, mp3, wav, webm)"),
                       conversation_id: str | None = Form(None), wait: bool = WaitQ, timeout: float = TimeoutQ):
    path, size = await _save_upload(file, MAX_AUDIO, "voice")
    job = job_queue.submit("voice", {"audio_path": str(path), "filename": file.filename, "bytes": size}, conversation_id)
    return await _respond(job, wait, timeout)


@app.post("/v1/email", tags=["input"], dependencies=[Depends(require_email_enabled), Depends(rate_limit)])
async def upload_email(file: UploadFile = File(..., description="File email .eml"),
                       conversation_id: str | None = Form(None), wait: bool = WaitQ, timeout: float = TimeoutQ):
    path, size = await _save_upload(file, MAX_EML, "email")
    job = job_queue.submit("email", {"eml_path": str(path), "filename": file.filename, "bytes": size, "origin": "upload"},
                           conversation_id)
    return await _respond(job, wait, timeout)


@app.post("/v1/email/imap/fetch", tags=["input"], dependencies=[Depends(require_email_enabled), Depends(rate_limit)])
async def imap_fetch():
    """Ambil email dari IMAP (konfigurasi .env, read-only) dan buat satu job per email."""
    if not settings.imap_enabled:
        raise HTTPException(400, "IMAP belum dikonfigurasi di .env (IMAP_HOST, IMAP_USER, IMAP_PASSWORD)")
    try:
        raws = await asyncio.to_thread(email_in.fetch_imap)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"IMAP gagal: {err(e)}") from e
    jobs, skipped = [], []
    for raw in raws:
        key = email_in.message_key(raw)
        dup = db.query_one("SELECT id FROM runs WHERE dedup_key=? AND status<>'error' AND COALESCE(outcome,'')<>'duplicate' "
                           "ORDER BY created_at LIMIT 1", (key,))
        if dup:
            skipped.append({"dedup_key": key, "run_sebelumnya": dup["id"]})
            continue
        path = Path(settings.upload_dir) / f"email_{db.new_id('imap')}.eml"
        path.write_bytes(raw)
        jobs.append(job_queue.submit("email", {"eml_path": str(path), "filename": path.name, "bytes": len(raw), "origin": "imap"}, None))
    return {"fetched": len(raws), "jobs": jobs, "skipped_duplicates": skipped}


# ------------------------------------------------------------------ jobs & runs & trace

@app.get("/v1/jobs", tags=["antrean"], dependencies=[Depends(require_key)])
async def list_jobs(status: str | None = None, limit: int = Query(50, le=500)):
    rows = db.query("SELECT id FROM jobs " + ("WHERE status=? " if status else "") + "ORDER BY created_at DESC LIMIT ?",
                    (status, limit) if status else (limit,))
    return {"queue_length": job_queue.length, "current_job": job_queue.current,
            "jobs": [job_queue.job_info(r["id"]) for r in rows]}


@app.get("/v1/jobs/{job_id}", tags=["antrean"], dependencies=[Depends(require_key)])
async def get_job(job_id: str):
    j = job_queue.job_info(job_id)
    if not j:
        raise HTTPException(404, "job tidak ditemukan")
    return j


@app.get("/v1/runs", tags=["trace"], dependencies=[Depends(require_key)])
async def list_runs(input_type: str | None = None, status: str | None = None, outcome: str | None = None, conversation_id: str | None = None,
                    limit: int = Query(50, le=500), offset: int = 0):
    w, p = [], []
    for col, v in (("input_type", input_type), ("status", status), ("outcome", outcome), ("conversation_id", conversation_id)):
        if v:
            w.append(f"{col}=?"); p.append(v)
    sql = ("SELECT id, job_id, conversation_id, input_type, input_text, status, outcome, final_answer, error, model, prompt_tokens, "
           f"completion_tokens, llm_calls, tool_calls, created_at, started_at, finished_at, duration_ms, {CONFIRM_LINK_COLS} FROM runs "
           + ("WHERE " + " AND ".join(w) + " " if w else "") + "ORDER BY created_at DESC LIMIT ? OFFSET ?")
    rows = db.query(sql, (*p, limit, offset))
    if rows:
        # input_meta ringkas (tanpa path server): nama file asli, + subjek untuk email
        ids = [r["id"] for r in rows]
        ph = ",".join("?" * len(ids))
        meta = {m["id"]: m for m in db.query(
            f"SELECT id, input_type, json_extract(input_meta_json, '$.filename') AS filename, "
            f"(SELECT json_extract(output_json, '$.subject') FROM steps WHERE steps.run_id=runs.id AND steps.name='baca_email' "
            f"LIMIT 1) AS subject FROM runs WHERE id IN ({ph})", ids)}
        for r in rows:
            m = meta.get(r["id"], {})
            r["input_meta"] = {"filename": m.get("filename")}
            if m.get("input_type") == "email":
                r["input_meta"]["subject"] = m.get("subject")
    return {"runs": rows}


@app.get("/v1/runs/{run_id}", tags=["trace"], dependencies=[Depends(require_key)])
async def get_run(run_id: str, include_messages: bool = False):
    r = get_run_summary(run_id)
    if not r:
        raise HTTPException(404, "run tidak ditemukan")
    r["steps"] = [step_row(s) for s in db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run_id,))]
    if include_messages:
        r["messages"] = db.loads(db.query_one("SELECT messages_json FROM runs WHERE id=?", (run_id,))["messages_json"])
    return r


def _run_upload_path(run_id: str, field: str) -> Path:
    """Path file unggahan asli sebuah run. Run jawaban konfirmasi (teks) memakai file dari run asalnya."""
    r = db.query_one("SELECT id, input_meta_json FROM runs WHERE id=?", (run_id,))
    if not r:
        raise HTTPException(404, "run tidak ditemukan")
    path = (db.loads(r["input_meta_json"]) or {}).get(field)
    if not path:
        ctx = db.query_one("SELECT output_json FROM steps WHERE run_id=? AND type='context' AND name='lanjutan_konfirmasi'", (run_id,))
        origin = (db.loads(ctx["output_json"]) or {}).get("run_asal") if ctx else None
        if origin and origin != run_id:
            o = db.query_one("SELECT input_meta_json FROM runs WHERE id=?", (origin,))
            path = (db.loads(o["input_meta_json"]) or {}).get(field) if o else None
    if not path:
        raise HTTPException(404, f"run ini tidak punya file {'gambar' if field == 'image_path' else 'audio'}")
    p = Path(path).resolve()
    if Path(settings.upload_dir).resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "file tidak tersedia")
    return p


def _file_response(p: Path, default_type: str) -> FileResponse:
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or default_type,
                        headers={"Cache-Control": "private, max-age=3600"})


@app.get("/v1/runs/{run_id}/image", tags=["trace"], dependencies=[Depends(require_key)])
async def run_image(run_id: str):
    """Gambar struk asli yang diunggah (run `receipt`, atau run jawaban konfirmasinya). Koordinat bbox step `ocr`
    mengacu ke gambar ini. Untuk `<img>` pakai `?key=API_KEY`."""
    return _file_response(_run_upload_path(run_id, "image_path"), "image/jpeg")


@app.get("/v1/runs/{run_id}/audio", tags=["trace"], dependencies=[Depends(require_key)])
async def run_audio(run_id: str):
    """Voice note asli (run `voice`, atau run jawaban konfirmasinya). Untuk `<audio>` pakai `?key=API_KEY`."""
    return _file_response(_run_upload_path(run_id, "audio_path"), "application/octet-stream")


@app.get("/v1/runs/{run_id}/stream", tags=["trace"], dependencies=[Depends(require_key)])
async def stream_run(run_id: str, request: Request):
    """SSE: kirim ulang langkah yang sudah ada, lalu langkah baru secara live sampai run selesai.
    Event: `step_started`, `llm_delta` (potongan thinking/jawaban live), `step`, `job_status`, `run_status`,
    `run_finished`."""
    if not db.query_one("SELECT id FROM runs WHERE id=?", (run_id,)):
        raise HTTPException(404, "run tidak ditemukan")
    topic = f"run:{run_id}"
    q = bus.subscribe(topic)

    async def gen():
        try:
            sent = 0
            for s in db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run_id,)):
                sent = s["seq"]
                yield {"event": "step", "data": json.dumps(step_row(s), ensure_ascii=False, default=str)}
            r = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
            if r["status"] in ("done", "done_with_warnings", "error", "awaiting_confirmation"):
                yield {"event": "run_finished", "data": json.dumps(get_run_summary(run_id), ensure_ascii=False, default=str)}
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                except asyncio.TimeoutError:
                    continue
                if ev["event"] == "step" and ev["data"]["seq"] <= sent:
                    continue
                yield {"event": ev["event"], "data": json.dumps(ev["data"], ensure_ascii=False, default=str)}
                if ev["event"] == "run_finished":
                    return
        finally:
            bus.unsubscribe(topic, q)

    return EventSourceResponse(gen(), ping=10)


@app.get("/v1/events", tags=["trace"], dependencies=[Depends(require_key)])
async def stream_all(request: Request):
    """SSE global: semua event dari semua run (untuk dashboard sandbox)."""
    q = bus.subscribe("global")

    async def gen():
        try:
            yield {"event": "hello", "data": json.dumps({"queue_length": job_queue.length})}
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                except asyncio.TimeoutError:
                    continue
                yield {"event": ev["event"], "data": json.dumps(ev["data"], ensure_ascii=False, default=str)}
        finally:
            bus.unsubscribe("global", q)

    return EventSourceResponse(gen(), ping=10)


# ------------------------------------------------------------------ transaksi (CRUD langsung, tanpa agent)

@app.get("/v1/transactions", tags=["transaksi"], dependencies=[Depends(require_key)])
async def list_transactions(jenis: str | None = None, kategori: str | None = None, kata_kunci: str | None = None,
                            periode: str | None = Query(None, description="hari_ini|kemarin|minggu_ini|bulan_ini|bulan_lalu|tahun_ini|semua"),
                            tanggal_mulai: str | None = None, tanggal_akhir: str | None = None,
                            source: str | None = None, limit: int = Query(100, le=1000)):
    try:
        res = tools.search_transactions({"jenis": jenis, "kategori": kategori, "kata_kunci": kata_kunci, "periode": periode,
                                         "tanggal_mulai": tanggal_mulai, "tanggal_akhir": tanggal_akhir, "batas": limit})
    except tools.ToolError as e:
        raise HTTPException(400, str(e)) from e
    if source:
        res["transaksi"] = [t for t in res["transaksi"] if t["source"] == source]
    return res


@app.get("/v1/transactions/{tx_id}", tags=["transaksi"], dependencies=[Depends(require_key)])
async def get_transaction(tx_id: int):
    try:
        return tools.get_transaction(tx_id)
    except tools.ToolError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/v1/transactions", tags=["transaksi"], dependencies=[Depends(require_key)], status_code=201)
async def create_transaction(body: TxIn):
    try:
        return tools.create_transaction(body.model_dump(), source="manual")
    except tools.ToolError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/v1/transactions/{tx_id}", tags=["transaksi"], dependencies=[Depends(require_key)])
async def patch_transaction(tx_id: int, body: TxPatch):
    try:
        return tools.update_transaction({"id": tx_id, **body.model_dump(exclude_none=True)}, strict=True)
    except tools.ToolError as e:
        raise HTTPException(400 if "tidak ditemukan" not in str(e) else 404, str(e)) from e


@app.delete("/v1/transactions/{tx_id}", tags=["transaksi"], dependencies=[Depends(require_key)])
async def delete_transaction(tx_id: int):
    try:
        return tools.delete_transaction(tx_id)
    except tools.ToolError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/v1/summary", tags=["transaksi"], dependencies=[Depends(require_key)])
async def get_summary(periode: str = "bulan_ini", tanggal_mulai: str | None = None, tanggal_akhir: str | None = None,
                      kategori: str | None = None):
    try:
        return tools.summary({"periode": periode, "tanggal_mulai": tanggal_mulai, "tanggal_akhir": tanggal_akhir, "kategori": kategori})
    except tools.ToolError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/v1/categories", tags=["transaksi"], dependencies=[Depends(require_key)])
async def categories():
    return [{"name": n, "kind": k, "description": d} for n, k, d in db.CATEGORIES]


# ------------------------------------------------------------------ metrik

@app.get("/v1/metrics", tags=["sistem"], dependencies=[Depends(require_key)])
async def get_metrics(window: int = Query(200, ge=1, le=5000)):
    return await metrics.collect(job_queue, window)


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()

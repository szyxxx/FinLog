"""Metrik: latency per jenis input, token/detik, antrean, GPU/VRAM (nvidia-smi), model termuat."""
import asyncio
import shutil
import statistics
import subprocess

from . import db, llm
from .inputs import stt


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, round(p / 100 * (len(vals) - 1))))
    return vals[k]


def gpu_stats() -> dict:
    if not shutil.which("nvidia-smi"):
        return {"available": False, "reason": "nvidia-smi tidak ditemukan"}
    try:
        q = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, check=True).stdout
        gpus = []
        for line in q.strip().splitlines():
            name, used, total, util, temp, power = [x.strip() for x in line.split(",")]
            num = lambda x: float(x) if x.replace(".", "", 1).isdigit() else None  # noqa: E731
            gpus.append({"name": name, "memory_used_mib": num(used), "memory_total_mib": num(total),
                         "utilization_pct": num(util), "temperature_c": num(temp), "power_w": num(power)})
        p = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        procs = []
        for line in p.strip().splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) == 3:
                procs.append({"pid": int(parts[0]), "name": parts[1], "used_memory_mib": float(parts[2]) if parts[2].isdigit() else parts[2]})
        return {"available": True, "gpus": gpus, "processes": procs}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": repr(e)}


async def collect(queue, window: int = 200) -> dict:
    runs = db.query("SELECT input_type, status, duration_ms, prompt_tokens, completion_tokens, created_at, started_at "
                    "FROM runs ORDER BY created_at DESC LIMIT ?", (window,))
    by_type: dict[str, dict] = {}
    for t in ("text", "receipt", "voice", "email"):
        rs = [r for r in runs if r["input_type"] == t and r["duration_ms"] is not None and r["status"] != "error"]
        durs = [r["duration_ms"] for r in rs]
        waits = [int((r["started_at"] - r["created_at"]) * 1000) for r in rs if r["started_at"]]
        by_type[t] = {
            "runs": len([r for r in runs if r["input_type"] == t]),
            "errors": len([r for r in runs if r["input_type"] == t and r["status"] == "error"]),
            "latency_ms": {"avg": round(statistics.mean(durs)) if durs else None, "p50": _pct(durs, 50), "p95": _pct(durs, 95),
                           "max": max(durs) if durs else None},
            "queue_wait_ms_avg": round(statistics.mean(waits)) if waits else None,
        }
    steps = db.query("SELECT s.type, s.name, s.duration_ms, s.prompt_tokens, s.completion_tokens, s.tokens_per_second "
                     "FROM steps s JOIN (SELECT id FROM runs ORDER BY created_at DESC LIMIT ?) r ON r.id=s.run_id "
                     "WHERE s.completion_tokens IS NOT NULL", (window,))
    tps = [s["tokens_per_second"] for s in steps if s["tokens_per_second"]]
    llm_ms = [s["duration_ms"] for s in steps if s["type"] == "llm"]
    stt_steps = db.query("SELECT duration_ms, output_json FROM steps WHERE name='transkripsi_suara' AND status='ok' "
                         "ORDER BY id DESC LIMIT 50")
    rtf = []
    for s in stt_steps:
        o = db.loads(s["output_json"]) or {}
        if o.get("audio_seconds"):
            rtf.append(s["duration_ms"] / 1000 / o["audio_seconds"])
    status_counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM runs GROUP BY status")}
    # Kunci tambahan (bukan status): pertanyaan konfirmasi yang SUDAH dijawab (ada run berikutnya di percakapan yang sama).
    # Nilai `awaiting_confirmation` lama tetap menghitung semuanya; yang masih terbuka = selisih keduanya.
    status_counts["awaiting_confirmation_answered"] = db.query_one(
        "SELECT COUNT(*) n FROM runs WHERE status='awaiting_confirmation' AND EXISTS (SELECT 1 FROM runs r2 WHERE "
        "r2.conversation_id=runs.conversation_id AND r2.created_at>runs.created_at)")["n"]
    ollama_models, gpu = await asyncio.gather(llm.ps(), asyncio.to_thread(gpu_stats))
    return {
        "window_runs": len(runs),
        "queue": {"length": queue.length, "current_job": queue.current},
        "runs_by_status": status_counts,
        "latency_by_input_type": by_type,
        "llm": {
            "calls": len(steps),
            "tokens_per_second_avg": round(statistics.mean(tps), 2) if tps else None,
            "tokens_per_second_p50": _pct(tps, 50),
            "llm_step_ms_avg": round(statistics.mean(llm_ms)) if llm_ms else None,
            "prompt_tokens_total": sum(s["prompt_tokens"] or 0 for s in steps),
            "completion_tokens_total": sum(s["completion_tokens"] or 0 for s in steps),
        },
        "stt": {**stt.model_info(), "real_time_factor_avg": round(statistics.mean(rtf), 3) if rtf else None},
        "ollama_loaded_models": [{"name": m.get("name"), "size_vram_bytes": m.get("size_vram"), "context_length": m.get("context_length"),
                                  "expires_at": m.get("expires_at")} for m in ollama_models],
        "gpu": gpu,
    }

"""Ekstrak trace nyata dari database server (READ-ONLY) menjadi fixture mock untuk web app.

Menulis src/mock/real.json dan menyalin gambar struk contoh ke public/mock/.
Tidak menyentuh server: database dibuka dengan mode=ro.

    python3 -I scripts/build_fixtures.py
"""
import json
import shutil
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # webapp/
SERVER = HERE.parent / "server"
OUT = HERE / "src" / "mock" / "real.json"
PUB = HERE / "public" / "mock"


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def j(s):
    return json.loads(s) if s else None


def step(r) -> dict:
    return {
        "id": r["id"], "run_id": r["run_id"], "seq": r["seq"], "type": r["type"], "name": r["name"],
        "status": r["status"], "input": j(r["input_json"]), "output": j(r["output_json"]),
        "started_at": r["started_at"], "duration_ms": r["duration_ms"],
        "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
        "tokens_per_second": r["tokens_per_second"],
    }


def tx(r) -> dict:
    return {
        "id": r["id"], "kind": r["kind"], "amount": r["amount"], "category": r["category"],
        "description": r["description"], "merchant": r["merchant"], "date": r["date"], "source": r["source"],
        "items": j(r["items_json"]), "run_id": r["run_id"], "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def run(r, steps, txs) -> dict:
    return {
        "id": r["id"], "job_id": r["job_id"], "conversation_id": r["conversation_id"],
        "input_type": r["input_type"], "input_text": r["input_text"], "input_meta": j(r["input_meta_json"]),
        "status": r["status"], "outcome": r["outcome"], "final_answer": r["final_answer"], "error": r["error"],
        "model": r["model"], "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
        "llm_calls": r["llm_calls"], "tool_calls": r["tool_calls"], "created_at": r["created_at"],
        "started_at": r["started_at"], "finished_at": r["finished_at"], "duration_ms": r["duration_ms"],
        "transactions": [t for t in txs if t["run_id"] == r["id"]],
        "steps": [s for s in steps if s["run_id"] == r["id"]],
    }


def main():
    PUB.mkdir(parents=True, exist_ok=True)
    out = {}

    db = ro(SERVER / "data" / "finance.db")
    steps = [step(r) for r in db.execute("select * from steps order by run_id, seq")]
    txs = [tx(r) for r in db.execute("select * from transactions where deleted = 0 order by id")]
    out["runs"] = [run(r, steps, txs) for r in db.execute("select * from runs order by created_at")]
    out["transactions"] = txs
    out["categories"] = [dict(r) for r in db.execute("select * from categories")]

    # Run struk dengan fallback vision dari benchmark (hanya step ekstraksi).
    bench = ro(SERVER / "data" / "bench_receipt.db")
    rid = bench.execute("select run_id from steps where type = 'fallback' limit 1").fetchone()[0]
    out["bench_fallback"] = {
        "run_id": rid,
        "steps": [step(r) for r in bench.execute("select * from steps where run_id = ? order by seq", (rid,))],
    }

    # Gambar struk per run untuk overlay bbox (server belum punya endpoint gambar).
    for old in PUB.glob("run_*"):
        old.unlink()
    out["images"] = {}
    for r in out["runs"]:
        meta = r.get("input_meta") or {}
        if r["input_type"] == "receipt" and meta.get("image_path"):
            src = Path(meta["image_path"])
            if src.exists():
                name = f"{r['id']}{src.suffix.lower()}"
                shutil.copy(src, PUB / name)
                out["images"][r["id"]] = f"mock/{name}"
    out["audio"] = {}
    for r in out["runs"]:
        meta = r.get("input_meta") or {}
        if r["input_type"] == "voice" and meta.get("audio_path") and Path(meta["audio_path"]).exists():
            src = Path(meta["audio_path"])
            name = f"{r['id']}{src.suffix.lower()}"
            shutil.copy(src, PUB / name)
            out["audio"][r["id"]] = f"mock/{name}"
    shutil.copy(SERVER / "samples" / "struk_serba_ada.png", PUB / "struk_serba_ada.png")
    shutil.copy(SERVER / "samples" / "bench" / "r2_resto_pajak.png", PUB / "struk_resto_pajak.png")

    # Fitur email sudah dihapus: buang run & transaksi email dari data mock.
    email_runs = {r["id"] for r in out["runs"] if r["input_type"] == "email"}
    out["runs"] = [r for r in out["runs"] if r["id"] not in email_runs]
    out["transactions"] = [t for t in out["transactions"] if t["source"] != "email"]

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"tulis {OUT} ({OUT.stat().st_size // 1024} KB); runs={len(out['runs'])}, tx={len(txs)}, bench={rid}")


if __name__ == "__main__":
    main()

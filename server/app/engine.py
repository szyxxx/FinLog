"""Pemilih mesin orkestrasi agent (AGENT_ENGINE di .env): `langgraph` (app/graph_agent.py) atau `custom` (app/agent.py).

Keduanya menulis kolom runs, step trace, dan event SSE yang sama. Kolom aditif `runs.engine` mencatat mesin yang dipakai.
"""
from . import db
from .config import get_settings


def active_engine() -> str:
    return get_settings().agent_engine


async def run_agent(run_id: str) -> None:
    # Graf LangGraph sengaja tidak punya node email (fitur email dinonaktifkan pemilik). Bila EMAIL_ENABLED=true
    # dipakai lagi, run email tetap dijalankan mesin custom (runs.engine = 'custom').
    is_email = (db.query_one("SELECT input_type FROM runs WHERE id=?", (run_id,)) or {}).get("input_type") == "email"
    if active_engine() == "langgraph" and not is_email:
        from .graph_agent import run_agent as run_lg
        await run_lg(run_id)
        return
    from .agent import run_agent as run_custom
    db.execute("UPDATE runs SET engine='custom' WHERE id=?", (run_id,))
    await run_custom(run_id)


async def startup() -> None:
    if active_engine() == "langgraph":
        from .graph_agent import get_graph
        await get_graph()  # muat LangGraph + checkpointer saat start agar kesalahan terlihat lebih awal


async def shutdown() -> None:
    import sys
    mod = sys.modules.get("app.graph_agent")
    if mod is not None:
        await mod.shutdown()

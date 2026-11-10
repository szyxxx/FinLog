"""Perekam trace run agent + event bus in-memory untuk SSE.

Setiap langkah disimpan ke tabel `steps` lalu dipublikasikan ke subscriber:
- per run   : /v1/runs/{id}/stream
- global    : /v1/events (semua run & perubahan status job)
"""
import asyncio
import contextvars
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

from . import db
from .util import err


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[topic].add(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        self._subs[topic].discard(q)

    def publish(self, topic: str, event: str, data: Any) -> None:
        for q in list(self._subs.get(topic, ())):
            try:
                q.put_nowait({"event": event, "data": data})
            except asyncio.QueueFull:
                pass


bus = EventBus()

# Nama node graf LangGraph yang sedang berjalan (diset oleh app/graph_agent.py). Mesin custom tidak mengisinya,
# sehingga kolom `node` pada step bernilai null.
CURRENT_NODE: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_node", default=None)


def publish(run_id: str | None, event: str, data: Any) -> None:
    if run_id:
        bus.publish(f"run:{run_id}", event, data)
    bus.publish("global", event, data)


def step_row(row: dict) -> dict:
    row = dict(row)
    row["input"] = db.loads(row.pop("input_json", None))
    row["output"] = db.loads(row.pop("output_json", None))
    return row


class Tracer:
    """Dipakai agent untuk mencatat langkah. Tipe langkah:
    input, preprocess (stt/vision/email), llm, tool, confirmation, final, error.
    """

    def __init__(self, run_id: str, seq_from_db: bool = False) -> None:
        self.run_id = run_id
        self.seq = 0
        if seq_from_db:
            row = db.query_one("SELECT MAX(seq) m FROM steps WHERE run_id=?", (run_id,))
            self.seq = (row and row["m"]) or 0

    def add(
        self,
        type: str,
        name: str | None = None,
        input: Any = None,
        output: Any = None,
        status: str = "ok",
        started_at: float | None = None,
        duration_ms: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        tokens_per_second: float | None = None,
        node: str | None = None,
    ) -> dict:
        self.seq += 1
        started_at = started_at or time.time()
        cur = db.execute(
            """INSERT INTO steps(run_id, seq, type, name, status, input_json, output_json, started_at,
               duration_ms, prompt_tokens, completion_tokens, tokens_per_second, node)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.run_id, self.seq, type, name, status, db.dumps(input), db.dumps(output), started_at,
             duration_ms, prompt_tokens, completion_tokens, tokens_per_second, node or CURRENT_NODE.get()),
        )
        step = step_row(db.query_one("SELECT * FROM steps WHERE id=?", (cur.lastrowid,)))
        publish(self.run_id, "step", step)
        return step

    def started(self, type: str, name: str | None, t0: float | None = None) -> None:
        ev = {"run_id": self.run_id, "type": type, "name": name, "started_at": t0 or time.time()}
        if CURRENT_NODE.get():
            ev["node"] = CURRENT_NODE.get()
        publish(self.run_id, "step_started", ev)

    def delta_publisher(self, name: str) -> "DeltaPublisher":
        return DeltaPublisher(self.run_id, name)

    @asynccontextmanager
    async def timed(self, type: str, name: str | None, input: Any):
        """Context manager: ukur durasi, simpan output/error. Isi `box['output']` (dan token opsional)."""
        box: dict[str, Any] = {}
        t0 = time.time()
        self.started(type, name, t0)
        try:
            yield box
        except Exception as e:  # noqa: BLE001
            self.add(type, name, input, {"error": err(e)}, status="error",
                     started_at=t0, duration_ms=int((time.time() - t0) * 1000))
            raise
        else:
            self.add(type, name, input, box.get("output"), status=box.get("status", "ok"), started_at=t0,
                     duration_ms=int((time.time() - t0) * 1000), prompt_tokens=box.get("prompt_tokens"),
                     completion_tokens=box.get("completion_tokens"), tokens_per_second=box.get("tokens_per_second"))


class DeltaPublisher:
    """Meneruskan potongan output LLM (thinking/content) ke SSE sebagai event `llm_delta`, dibatch ±150 ms.
    Potongan ini tidak disimpan per token; teks lengkapnya disimpan di step `reasoning` / `llm`."""

    def __init__(self, run_id: str, name: str, interval: float = 0.15) -> None:
        self.run_id, self.name, self.interval = run_id, name, interval
        self.buf: dict[str, list[str]] = {"thinking": [], "content": []}
        self.last = time.time()
        self.index = 0
        self.node = CURRENT_NODE.get()

    def feed(self, kind: str, text: str) -> None:
        self.buf[kind].append(text)
        if time.time() - self.last >= self.interval:
            self.flush()

    def flush(self) -> None:
        for kind in ("thinking", "content"):
            if self.buf[kind]:
                ev = {"run_id": self.run_id, "step_name": self.name, "kind": kind, "index": self.index,
                      "text": "".join(self.buf[kind])}
                if self.node:
                    ev["node"] = self.node
                publish(self.run_id, "llm_delta", ev)
                self.index += 1
                self.buf[kind] = []
        self.last = time.time()

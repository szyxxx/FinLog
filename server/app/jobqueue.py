"""Antrean job sederhana: satu worker (satu GPU) memproses run secara berurutan (FIFO)."""
import asyncio
import logging
import time

from . import db
from .agent import get_run_summary
from .engine import run_agent
from .config import get_settings
from .trace import Tracer, publish
from .util import err

log = logging.getLogger("queue")


class JobQueue:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.order: list[str] = []  # job_id menunggu, urut
        self.current: str | None = None
        self.done_events: dict[str, asyncio.Event] = {}
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()

    def submit(self, input_type: str, payload: dict, conversation_id: str | None) -> dict:
        job_id, run_id = db.new_id("job"), db.new_id("run")
        conv = conversation_id or db.new_id("conv")
        now = time.time()
        db.execute("INSERT INTO jobs(id, input_type, status, run_id, payload_json, created_at) VALUES (?,?,?,?,?,?)",
                   (job_id, input_type, "queued", run_id, db.dumps(payload), now))
        db.execute("INSERT INTO runs(id, job_id, conversation_id, input_type, input_meta_json, status, created_at) "
                   "VALUES (?,?,?,?,?,?,?)", (run_id, job_id, conv, input_type, db.dumps(payload), "queued", now))
        self.order.append(job_id)
        self.done_events[job_id] = asyncio.Event()
        self.queue.put_nowait(job_id)
        info = self.job_info(job_id)
        publish(run_id, "job_status", info)
        return info

    def position(self, job_id: str) -> int | None:
        if job_id == self.current:
            return 0
        try:
            return self.order.index(job_id) + 1
        except ValueError:
            return None

    def job_info(self, job_id: str) -> dict | None:
        j = db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not j:
            return None
        j["payload"] = db.loads(j.pop("payload_json", None))
        r = db.query_one("SELECT conversation_id FROM runs WHERE id=?", (j["run_id"],))
        j["conversation_id"] = r["conversation_id"] if r else None
        j["queue_position"] = self.position(job_id) if j["status"] in ("queued", "running") else None
        j["wait_ms"] = int(((j["started_at"] or time.time()) - j["created_at"]) * 1000) if j["status"] != "queued" else None
        j["run_ms"] = int((j["finished_at"] - j["started_at"]) * 1000) if j["finished_at"] and j["started_at"] else None
        return j

    @property
    def length(self) -> int:
        return len(self.order)

    async def wait(self, job_id: str, timeout: float) -> bool:
        ev = self.done_events.get(job_id)
        if ev is None:
            return True
        try:
            await asyncio.wait_for(ev.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            if job_id in self.order:
                self.order.remove(job_id)
            self.current = job_id
            j = db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
            db.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (time.time(), job_id))
            publish(j["run_id"], "job_status", self.job_info(job_id))
            try:
                limit = get_settings().agent_run_timeout_s
                try:
                    await asyncio.wait_for(run_agent(j["run_id"]), limit)
                except asyncio.TimeoutError:
                    msg = f"timeout: run melebihi AGENT_RUN_TIMEOUT_S={limit:.0f} detik"
                    now = time.time()
                    db.execute("UPDATE runs SET status='error', error=?, finished_at=?, "
                               "duration_ms=CAST((? - COALESCE(started_at, ?)) * 1000 AS INTEGER) WHERE id=?",
                               (msg, now, now, now, j["run_id"]))
                    Tracer(j["run_id"], seq_from_db=True).add("error", "Timeout", None, {"error": msg}, status="error")
                    publish(j["run_id"], "run_finished", get_run_summary(j["run_id"]))
                r = db.query_one("SELECT status, error FROM runs WHERE id=?", (j["run_id"],))
                st = "error" if r["status"] == "error" else "done"
                db.execute("UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?", (st, r["error"], time.time(), job_id))
            except Exception as e:  # noqa: BLE001
                log.exception("job %s gagal", job_id)
                db.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (err(e), time.time(), job_id))
            finally:
                self.current = None
                publish(j["run_id"], "job_status", self.job_info(job_id))
                ev = self.done_events.pop(job_id, None)
                if ev:
                    ev.set()


job_queue = JobQueue()

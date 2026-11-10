"""Klien Ollama (/api/chat, streaming) dengan statistik token, durasi, dan fase thinking.

Streaming dipakai agar potongan reasoning ("thinking") dan jawaban bisa diteruskan live ke SSE lewat `on_delta`.
"""
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .config import get_settings

_client: httpx.AsyncClient | None = None

# on_delta(kind, text): kind = "thinking" | "content"
DeltaCallback = Callable[[str, str], Awaitable[None] | None]


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        s = get_settings()
        # read timeout = jeda maksimum antar-chunk stream (bukan total durasi)
        _client = httpx.AsyncClient(base_url=s.ollama_url, timeout=httpx.Timeout(s.llm_read_timeout_s, connect=10))
    return _client


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    format: Any = None,
    think: bool | None = None,
    temperature: float | None = None,
    on_delta: DeltaCallback | None = None,
) -> dict:
    s = get_settings()
    think = s.llm_think if think is None else think
    body: dict[str, Any] = {
        "model": model or s.llm_model,
        "messages": messages,
        "stream": True,
        "think": think,
        "keep_alive": s.llm_keep_alive,
        "options": {
            "temperature": s.llm_temperature if temperature is None else temperature,
            "num_ctx": s.llm_num_ctx,
            "num_predict": s.llm_num_predict,
        },
    }
    if tools:
        body["tools"] = tools
    if format is not None:
        body["format"] = format

    t0 = time.time()
    thinking: list[str] = []
    content: list[str] = []
    tool_calls: list[dict] = []
    t_first_token = t_think_end = None
    final: dict = {}
    async with client().stream("POST", "/api/chat", json=body) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama error {r.status_code}: {(await r.aread())[:500]!r}")
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(f"Ollama error: {d['error']}")
            m = d.get("message") or {}
            now = time.time()
            if m.get("thinking"):
                t_first_token = t_first_token or now
                thinking.append(m["thinking"])
                if on_delta:
                    res = on_delta("thinking", m["thinking"])
                    if res is not None:
                        await res
            if m.get("content") or m.get("tool_calls"):
                t_first_token = t_first_token or now
                if thinking and t_think_end is None:
                    t_think_end = now
            if m.get("content"):
                content.append(m["content"])
                if on_delta:
                    res = on_delta("content", m["content"])
                    if res is not None:
                        await res
            if m.get("tool_calls"):
                tool_calls.extend(m["tool_calls"])
            if d.get("done"):
                final = d
                break
    wall = time.time() - t0
    if thinking and t_think_end is None:
        t_think_end = time.time()  # model hanya berpikir (mis. terpotong num_predict)
    eval_count = final.get("eval_count") or 0
    eval_dur = (final.get("eval_duration") or 0) / 1e9
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if thinking:
        message["thinking"] = "".join(thinking)
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "message": message,
        "model": final.get("model"),
        "done_reason": final.get("done_reason"),
        "prompt_tokens": final.get("prompt_eval_count") or 0,
        "completion_tokens": eval_count,
        "tokens_per_second": round(eval_count / eval_dur, 2) if eval_dur > 0 else None,
        "think": think,
        "timings_ms": {
            "wall": int(wall * 1000),
            "total": int((final.get("total_duration") or 0) / 1e6),
            "load": int((final.get("load_duration") or 0) / 1e6),
            "prompt_eval": int((final.get("prompt_eval_duration") or 0) / 1e6),
            "eval": int(eval_dur * 1000),
            "time_to_first_token": int((t_first_token - t0) * 1000) if t_first_token else None,
            "thinking": int((t_think_end - t_first_token) * 1000) if thinking and t_first_token else 0,
        },
    }


async def ps() -> list[dict]:
    try:
        r = await client().get("/api/ps", timeout=5)
        return r.json().get("models", [])
    except Exception:  # noqa: BLE001
        return []


async def show(model: str) -> dict:
    r = await client().post("/api/show", json={"model": model}, timeout=10)
    r.raise_for_status()
    return r.json()

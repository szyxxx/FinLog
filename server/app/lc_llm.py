"""Panggilan model lewat LangChain `ChatOllama` (dipakai mesin LangGraph).

Mengembalikan dict berbentuk SAMA dengan `llm.chat` (message/thinking/tool_calls, token, timings_ms), sehingga
`agent.llm_step` dan trace (step `reasoning` + `llm`, SSE `llm_delta`) tidak berubah. Reasoning qwen3.5 datang terpisah
di `additional_kwargs["reasoning_content"]` (ChatOllama `reasoning=True`).

Pesan tetap disimpan dalam format dict Ollama (role/content/tool_calls/tool_name/images) seperti mesin custom, agar riwayat
percakapan di database bisa dipakai bergantian oleh kedua mesin. Konversi ke pesan LangChain dilakukan di sini.
"""
import os

# Tanpa telemetri keluar: data keuangan tetap lokal. Dipaksa mati sebelum modul LangChain dimuat.
for _k in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING_V2"):
    os.environ[_k] = "false"

import time  # noqa: E402
from functools import lru_cache  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from langchain_ollama import ChatOllama  # noqa: E402

from .config import get_settings  # noqa: E402
from .llm import DeltaCallback  # noqa: E402


@lru_cache(maxsize=16)
def _model(model: str, think: bool, temperature: float) -> ChatOllama:
    s = get_settings()
    timeout = httpx.Timeout(s.llm_read_timeout_s, connect=10)  # read = jeda maksimum antar-chunk stream
    return ChatOllama(
        model=model, base_url=s.ollama_url, reasoning=think, temperature=temperature, num_ctx=s.llm_num_ctx,
        num_predict=s.llm_num_predict, keep_alive=s.llm_keep_alive, validate_model_on_init=False,
        client_kwargs={"timeout": timeout},
    )


def to_lc_messages(messages: list[dict]) -> list[BaseMessage]:
    """Dict format Ollama -> pesan LangChain. tool_call_id dipasangkan berurutan dengan tool_calls asisten sebelumnya."""
    out: list[BaseMessage] = []
    pending: list[tuple[str, str]] = []  # (id, nama) tool call yang belum dijawab
    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if role == "system":
            out.append(SystemMessage(content))
        elif role == "user":
            if m.get("images"):
                parts: list[Any] = [{"type": "text", "text": content}]
                parts += [{"type": "image_url", "image_url": f"data:image/jpeg;base64,{img}"} for img in m["images"]]
                out.append(HumanMessage(content=parts))
            else:
                out.append(HumanMessage(content))
        elif role == "assistant":
            tcs = []
            for i, tc in enumerate(m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                args = fn.get("arguments") or {}
                tcs.append({"name": fn.get("name") or "", "args": args if isinstance(args, dict) else {"_raw": args},
                            "id": tc.get("id") or f"call_{len(out)}_{i}", "type": "tool_call"})
            pending = [(t["id"], t["name"]) for t in tcs]
            out.append(AIMessage(content=content, tool_calls=tcs))
        elif role == "tool":
            name = m.get("tool_name") or ""
            idx = next((k for k, (_, n) in enumerate(pending) if n == name), 0 if pending else None)
            tc_id = pending.pop(idx)[0] if idx is not None else f"call_orphan_{len(out)}"
            out.append(ToolMessage(content=content, tool_call_id=tc_id, name=name or None))
    return out


def _ollama_tool_calls(msg: AIMessage) -> list[dict]:
    """tool_calls LangChain -> bentuk Ollama yang sama dengan trace mesin custom."""
    return [{"id": tc.get("id"), "function": {"index": i, "name": tc["name"], "arguments": tc.get("args") or {}}}
            for i, tc in enumerate(msg.tool_calls or [])]


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
    temp = s.llm_temperature if temperature is None else temperature
    runnable: Any = _model(model or s.llm_model, bool(think), float(temp))
    if tools:
        runnable = runnable.bind_tools(tools)
    kwargs: dict[str, Any] = {}
    if format is not None:
        kwargs["format"] = format

    t0 = time.time()
    t_first_token = t_think_end = None
    agg = None
    thinking_seen = False
    async for ch in runnable.astream(to_lc_messages(messages), **kwargs):
        now = time.time()
        th = ch.additional_kwargs.get("reasoning_content")
        if th:
            t_first_token = t_first_token or now
            thinking_seen = True
            if on_delta:
                r = on_delta("thinking", th)
                if r is not None:
                    await r
        text = ch.content if isinstance(ch.content, str) else ""
        if text or ch.tool_call_chunks or ch.tool_calls:
            t_first_token = t_first_token or now
            if thinking_seen and t_think_end is None:
                t_think_end = now
        if text and on_delta:
            r = on_delta("content", text)
            if r is not None:
                await r
        agg = ch if agg is None else agg + ch
    wall = time.time() - t0
    if thinking_seen and t_think_end is None:
        t_think_end = time.time()
    if agg is None:
        raise RuntimeError("Ollama tidak mengembalikan respons")
    meta = agg.response_metadata or {}
    thinking = agg.additional_kwargs.get("reasoning_content") or ""
    content = agg.content if isinstance(agg.content, str) else str(agg.content)
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    if agg.tool_calls:
        message["tool_calls"] = _ollama_tool_calls(agg)
    eval_count = meta.get("eval_count") or 0
    eval_dur = (meta.get("eval_duration") or 0) / 1e9
    return {
        "message": message,
        "model": meta.get("model") or meta.get("model_name"),
        "done_reason": meta.get("done_reason"),
        "prompt_tokens": meta.get("prompt_eval_count") or 0,
        "completion_tokens": eval_count,
        "tokens_per_second": round(eval_count / eval_dur, 2) if eval_dur > 0 else None,
        "think": think,
        "timings_ms": {
            "wall": int(wall * 1000),
            "total": int((meta.get("total_duration") or 0) / 1e6),
            "load": int((meta.get("load_duration") or 0) / 1e6),
            "prompt_eval": int((meta.get("prompt_eval_duration") or 0) / 1e6),
            "eval": int(eval_dur * 1000),
            "time_to_first_token": int((t_first_token - t0) * 1000) if t_first_token else None,
            "thinking": int((t_think_end - t_first_token) * 1000) if thinking and t_first_token else 0,
        },
    }




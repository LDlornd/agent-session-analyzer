"""Map dsh v3 SessionEvents to an analysis envelope; preserve the source event."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from ..schema import SCHEMA
TOKEN_FIELDS = {
    "inputTokens": "input_tokens", "outputTokens": "output_tokens",
    "totalTokens": "total_tokens", "cacheReadTokens": "cache_read_tokens",
    "cacheWriteTokens": "cache_write_tokens", "reasoningTokens": "reasoning_tokens",
}


def content_text(blocks: Any, kind: str = "text") -> str | None:
    if not isinstance(blocks, list):
        return None
    parts = [b["text"] for b in blocks if isinstance(b, dict)
             and b.get("type") == kind and isinstance(b.get("text"), str)]
    return "".join(parts) if parts else None


def usage_fields(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    # Do not infer totals or add reasoning/cache subtotals a second time.
    return {target: raw.get(source) for source, target in TOKEN_FIELDS.items()}


def stream_info(stream: Any) -> dict:
    """Use terminal usage only once; packed text is a fallback for failed attempts."""
    usage, finish = None, None
    reasoning, text, times = [], [], []
    for item in stream if isinstance(stream, list) else []:
        if not isinstance(item, dict):
            continue
        for key in ("time", "time0"):
            if isinstance(item.get(key), (int, float)):
                times.append(item[key])
        if isinstance(item.get("time0"), (int, float)) and isinstance(item.get("dt"), list):
            times.append(item["time0"] + sum(x for x in item["dt"] if isinstance(x, (int, float))))
        if item.get("type") in ("reasoning-chunks", "text-chunks"):
            target = reasoning if item["type"] == "reasoning-chunks" else text
            target.extend(x for x in item.get("texts", []) if isinstance(x, str))
        chunk = item.get("chunk", {})
        if not isinstance(chunk, dict):
            continue
        if chunk.get("type") == "usage":
            usage = chunk.get("usage")
        elif chunk.get("type") == "finish":
            finish = chunk.get("reason")
        elif chunk.get("type") in ("reasoning-delta", "text-delta"):
            target = reasoning if chunk["type"] == "reasoning-delta" else text
            value = chunk.get("text", chunk.get("delta"))
            if isinstance(value, str):
                target.append(value)
    return {"usage": usage, "finish_reason": finish,
            "reasoning": "".join(reasoning) or None, "text": "".join(text) or None,
            "first_stream_time_ms": min(times) if times else None,
            "last_stream_time_ms": max(times) if times else None}


class DshAdapter:
    def __init__(self, session_id: str, model: dict | None = None):
        self.session_id = session_id
        self.model = dict(model or {})
        self.turn = self.step = None
        self.attempts = defaultdict(int)
        self.last_attempt = {}
        self.seen = {}

    def feed(self, event: dict) -> dict | None:
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ValueError("Expected a dsh event object with a type")
        seq = event.get("seq")
        fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if seq is not None:
            if seq in self.seen:
                if self.seen[seq] != fingerprint:
                    raise ValueError(f"Conflicting dsh event at seq={seq}")
                return None
            self.seen[seq] = fingerprint
        kind = event["type"]
        data = event.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("dsh event data must be an object")
        if kind == "turn/start":
            self.turn, self.step = data.get("turn"), None
        if "turn" in data:
            self.turn = data["turn"]
        if "step" in data:
            self.step = data["step"]
        turn, step = self.turn, self.step
        if kind.startswith("turn/"):
            step = None
        if kind == "request/context":
            self.model.update({k: data[k] for k in ("provider", "model", "contextWindow") if k in data})
        if kind == "request/header":
            config = (data.get("header") or {}).get("config") or {}
            self.model.update({k: config[k] for k in ("provider", "model", "reasoningEffort", "maxTokens") if k in config})

        row = {"schema": SCHEMA, "harness": "dsh", "session_id": self.session_id,
               "event_id": f"{self.session_id}:{seq if seq is not None else fingerprint}",
               "source_seq": seq, "time_ms": event.get("time"),
               "event": kind, "turn_id": turn, "step_id": step,
               "attempt_id": None, "tool_call_id": None, "model": dict(self.model),
               "data": dict(data), "raw": event}
        if kind in ("assistant/message", "assistant/attempt"):
            key = (turn, step)
            self.attempts[key] += 1
            attempt_id = f"{turn}/{step}/{self.attempts[key]}"
            self.last_attempt[key] = attempt_id
            info = stream_info(data.get("stream"))
            message = data.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else info["usage"]
            row.update(event="model.attempt", attempt_id=attempt_id)
            row["data"] = {
                "attempt_index": self.attempts[key],
                "status": "interrupted" if data.get("interrupted") else
                          ("committed" if kind == "assistant/message" else "uncommitted"),
                "usage": usage_fields(usage), "usage_raw": usage,
                "usage_source": "dsh_adapter" if usage is not None else "unavailable",
                "reasoning": content_text(content, "reasoning") or info["reasoning"],
                "text": content_text(content) or info["text"],
                "finish_reason": info["finish_reason"],
                "first_stream_time_ms": info["first_stream_time_ms"],
                "last_stream_time_ms": info["last_stream_time_ms"],
            }
        elif kind == "tool/call":
            row.update(event="tool.call", tool_call_id=data.get("callId"),
                       attempt_id=self.last_attempt.get((turn, step)))
        elif kind == "tool/result":
            blocks = (data.get("message") or {}).get("content") or []
            result = next((b for b in blocks if isinstance(b, dict) and b.get("type") == "tool-result"), {})
            row.update(event="tool.result", tool_call_id=result.get("toolCallId"),
                       attempt_id=self.last_attempt.get((turn, step)))
            row["data"] = {"content": result.get("content"), "is_error": result.get("isError"),
                           "error": data.get("error"), "message": data.get("message"),
                           "source_event_seqs": event.get("sourceEventSeqs")}
        if kind in ("step/end", "turn/end"):
            self.step = None
        if kind == "turn/end":
            self.turn = None
        return row

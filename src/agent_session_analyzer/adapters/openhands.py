"""Pure OpenHands event/completion adapter; importing this module needs no SDK.

Steps are derived response/tool groups, NOT native Agent.step() invocations.
Only the local synchronous SDK callback contract is currently supported.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from ..schema import SCHEMA


def now_ms():
    return time.time_ns() // 1_000_000


def as_dict(value):
    if isinstance(value, dict):
        return value
    return value.model_dump(mode="json")


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict)
                       and isinstance(b.get("text"), str)) or None
    return None


def first_value(*values):
    return next((v for v in values if v is not None), None)


def normalize_usage(usage):
    u = usage if isinstance(usage, dict) else {}
    inp = u.get("prompt_tokens_details") or u.get("input_tokens_details") or {}
    out = u.get("completion_tokens_details") or u.get("output_tokens_details") or {}
    return {
        "input_tokens": first_value(u.get("prompt_tokens"), u.get("input_tokens")),
        "output_tokens": first_value(u.get("completion_tokens"), u.get("output_tokens")),
        "total_tokens": u.get("total_tokens"),
        "cache_read_tokens": first_value(inp.get("cached_tokens"), u.get("cache_read_input_tokens")),
        "cache_write_tokens": first_value(inp.get("cache_write_tokens"), inp.get("cache_creation_tokens"), u.get("cache_creation_input_tokens")),
        "reasoning_tokens": out.get("reasoning_tokens"),
    }


def message_fields(message):
    blocks = message.get("thinking_blocks") or []
    visible = [b.get("thinking") for b in blocks if isinstance(b, dict)
               and b.get("type") == "thinking" and isinstance(b.get("thinking"), str)]
    item = message.get("responses_reasoning_item") or {}
    return {
        "text": text_content(message.get("content")),
        "reasoning": first_value(message.get("reasoning_content"), "".join(visible) or None,
                                 text_content(item.get("summary"))),
        "reasoning_fields": {k: message[k] for k in
                             ("reasoning_content", "thinking_blocks", "responses_reasoning_item") if k in message},
    }


def response_fields(response):
    choices = response.get("choices") or []
    if choices:
        choice = choices[0]
        message = choice.get("message") or {}
        calls = message.get("tool_calls") or []
        return {**message_fields(message), "finish_reason": choice.get("finish_reason"),
                "tool_ids": [c["id"] for c in calls if isinstance(c, dict) and c.get("id")]}
    text, reasoning, tools = [], [], []
    for item in response.get("output") or []:
        if item.get("type") == "message":
            text.append(text_content(item.get("content")) or "")
        elif item.get("type") == "reasoning":
            reasoning.append(text_content(item.get("summary")) or "")
        elif item.get("type") == "function_call" and item.get("call_id"):
            tools.append(item["call_id"])
    return {"text": "".join(text) or None, "reasoning": "".join(reasoning) or None,
            "reasoning_fields": {"responses_output": response.get("output")},
            "finish_reason": response.get("status"), "tool_ids": tools}


class OpenHandsAdapter:
    """Feed native events, completion JSON and retry callbacks into one schema.

    Methods return lists of new immutable-by-convention records. Keep a single
    adapter per conversation. Call begin_turn/end_turn around one run invocation.
    """

    def __init__(self, session_id, model=None):
        self.session_id = str(session_id)
        self.model = dict(model or {})
        self.turn = 0
        self.active = False
        self.step_count = 0
        self.groups = []
        self.responses = {}
        self.pending_retries = {}
        self.tools = {}
        self.seen_events = set()
        self.seen_logs = set()

    def row(self, kind, data, *, group=None, raw=None, when=None, attempt=None, tool=None):
        return {"schema": SCHEMA, "harness": "openhands", "session_id": self.session_id,
                "event_id": uuid.uuid4().hex, "source_seq": None,
                "time_ms": now_ms() if when is None else when,
                "event": kind, "turn_id": group["turn"] if group else (self.turn if self.active else None),
                "step_id": group["step"] if group else None, "attempt_id": attempt,
                "tool_call_id": tool, "model": dict(group["model"] if group else self.model),
                "data": data, "raw": raw}

    def begin_turn(self, task):
        if self.active:
            raise RuntimeError("A turn is already active")
        self.turn += 1
        self.active = True
        self.step_count = 0
        self.pending_retries.clear()
        return [self.row("turn/start", {"turn": self.turn, "boundary_source": "recorder.run", "input": task})]

    def _group(self, rows, model):
        self.step_count += 1
        group = {"turn": self.turn, "step": self.step_count, "model": dict(model),
                 "attempts": 0, "recorded": False, "closed": False, "pending": set(), "message": {}}
        self.groups.append(group)
        start = self.row("step/start", {"boundary_source": "derived_response_group"}, group=group)
        start["time_ms"] = None  # No request-start hook: do not invent a step duration.
        rows.append(start)
        return group

    def _attempt(self, group, data, raw=None):
        group["attempts"] += 1
        aid = f"{group['turn']}/{group['step']}/{group['attempts']}"
        group["attempt_id"] = aid
        return self.row("model.attempt", {"attempt_index": group["attempts"], **data},
                        group=group, attempt=aid, raw=raw)

    def retry(self, llm_key, attempt_number, num_retries, error, model=None):
        if not self.active or error is None:
            return []
        rows = []
        group = self.pending_retries.get(llm_key)
        if group is None:
            group = self._group(rows, model or self.model)
            self.pending_retries[llm_key] = group
        rows.append(self._attempt(group, {
            "status": "failed", "usage": normalize_usage(None), "usage_raw": None,
            "usage_source": "unavailable", "reasoning": None, "text": None,
            "error": {"type": type(error).__name__, "message": str(error)},
            "sdk_attempt_number": attempt_number, "sdk_num_retries": num_retries,
            "finish_reason": "retry_scheduled"}))
        return rows

    def completion(self, filename, log_data, *, llm_key="main", model=None):
        if not self.active:
            return []
        log = json.loads(log_data) if isinstance(log_data, str) else as_dict(log_data)
        key = (self.turn, llm_key, filename)
        if key in self.seen_logs:
            return []
        self.seen_logs.add(key)
        response = log.get("response") or {}
        rid = response.get("id")
        rows = []
        group = self.responses.get(rid) if rid else None
        if group and group["recorded"]:
            return []
        if group is None:
            group = self.pending_retries.pop(llm_key, None)
        if group is None:
            group = self._group(rows, model or self.model)
        if rid:
            self.responses[rid] = group
        group["model"].update(model or {})
        if response.get("model"):
            group["model"]["model"] = response["model"]
        group["model"]["usage_id"] = llm_key
        fields = response_fields(response)
        group["pending"].update(fields.pop("tool_ids"))
        usage = response.get("usage")
        error = log.get("error")
        rows.append(self._attempt(group, {
            **fields, "status": "failed" if error else "completed",
            "response_id": rid, "usage": normalize_usage(usage), "usage_raw": usage,
            "usage_source": "response" if isinstance(usage, dict) else "unavailable",
            "latency_sec": log.get("latency_sec"), "cost": log.get("cost"), "error": error,
        }, raw=log))
        group["recorded"] = True
        return rows

    def event(self, event):
        raw = as_dict(event)
        if raw.get("id") in self.seen_events:
            return []
        if raw.get("id"):
            self.seen_events.add(raw["id"])
        kind = raw.get("kind") or raw.get("type") or type(event).__name__
        when = now_ms()
        if isinstance(raw.get("timestamp"), str):
            try:
                when = int(datetime.fromisoformat(raw["timestamp"]).timestamp() * 1000)
            except ValueError:
                pass
        if not self.active:
            return [self.row("openhands.event", {"kind": kind}, raw=raw, when=when)]
        rows = []
        rid = raw.get("llm_response_id")
        group = self.responses.get(rid) if rid else None
        if rid and group is None:
            group = self._group(rows, self.model)
            self.responses[rid] = group
            # Reserve the first attempt identity; emit a placeholder at end_turn
            # only if no completion log arrives for this response.
            group["attempt_id"] = f"{group['turn']}/{group['step']}/1"
        if kind == "MessageEvent" and raw.get("source") == "user":
            message = raw.get("llm_message") or {}
            rows.append(self.row("user/message", {"content": message.get("content")}, raw=raw, when=when))
        elif kind == "ActionEvent":
            if group is None:
                group = self._group(rows, self.model)
            group["message"] = {**message_fields(raw), "thought": raw.get("thought")}
            cid = raw.get("tool_call_id")
            aid = group.get("attempt_id")
            if cid:
                group["pending"].add(cid)
                self.tools[cid] = (group, aid)
            call = raw.get("tool_call") or {}
            arguments = (call.get("function") or {}).get("arguments")
            rows.append(self.row("tool.call", {"name": raw.get("tool_name"),
                "arguments": arguments if arguments is not None else raw.get("action"),
                "thought": raw.get("thought"), "reasoning_fields": group["message"],
                "executable": raw.get("action") is not None},
                group=group, tool=cid, attempt=aid, raw=raw, when=when))
        elif kind in ("ObservationEvent", "AgentErrorEvent", "UserRejectObservation"):
            cid = raw.get("tool_call_id")
            group, aid = self.tools.get(cid, (None, None))
            observation = raw.get("observation") or {}
            if kind == "ObservationEvent":
                content = observation
                is_error = observation.get("is_error")
            else:
                content = raw.get("error") or raw.get("rejection_reason")
                is_error = True
            rows.append(self.row("tool.result", {"content": content, "is_error": is_error,
                "error": raw.get("error"), "rejected": kind == "UserRejectObservation",
                "action_id": raw.get("action_id"), "extended_content": raw.get("extended_content")},
                group=group, tool=cid, attempt=aid, raw=raw, when=when))
            if group:
                group["pending"].discard(cid)
        else:
            if kind == "MessageEvent" and group:
                group["message"] = message_fields(raw.get("llm_message") or {})
            rows.append(self.row("openhands.event", {"kind": kind}, group=group, raw=raw, when=when))
        return rows

    def end_turn(self, status):
        if not self.active:
            return []
        rows = []
        for group in self.groups:
            if group["closed"] or group["turn"] != self.turn:
                continue
            if not group["recorded"] and group["attempts"] == 0:
                rows.append(self._attempt(group, {**group["message"],
                    "status": "event_only", "usage": normalize_usage(None), "usage_raw": None,
                    "usage_source": "unavailable", "finish_reason": None}))
                rows.append(self.row("capture.warning", {"message": "未观测到此响应的 completion 日志，token 未知。"}, group=group))
            elif not group["recorded"]:
                rows.append(self.row("capture.warning", {"message": "仅观测到重试回调，缺少最终 completion 日志。"}, group=group))
            rows.append(self.row("step/end", {"boundary_source": "recorder.run_return",
                "pending_tool_ids": sorted(group["pending"]), "run_status": status}, group=group))
            group["closed"] = True
        rows.append(self.row("turn/end", {"reason": {"kind": status}, "boundary_source": "recorder.run"}))
        self.active = False
        return rows

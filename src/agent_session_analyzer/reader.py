"""Offline readers for harness-trace/1 and uncompressed dsh session v3."""
from __future__ import annotations

import json
from pathlib import Path

from .adapter import SCHEMA, TOKEN_FIELDS, DshAdapter

MAX_FILE_BYTES = 100 * 1024 * 1024


def parse_jsonl(text: str) -> tuple[list[dict], list[str]]:
    rows, warnings = [], []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            if index == len(lines) - 1 and not text.endswith(("\n", "\r")):
                warnings.append(f"第 {index + 1} 行尚未写完，已忽略；刷新可重新读取。")
                break
            raise ValueError(f"第 {index + 1} 行不是有效 JSON：{error.msg}") from error
        if not isinstance(row, dict):
            raise ValueError(f"第 {index + 1} 行必须是 JSON 对象")
        rows.append(row)
    return rows, warnings


def read_trace(path: str | Path) -> dict:
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("文件超过 100 MiB，请按 session 拆分后查看")
    with path.open("rb") as file:
        content = file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("文件超过 100 MiB")
    # A live append can be cut in the middle of a multibyte UTF-8 character.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        if error.end == len(content) and error.reason == "unexpected end of data":
            text = content[:error.start].decode("utf-8-sig")
        else:
            raise ValueError("日志不是有效 UTF-8") from error
    rows, warnings = parse_jsonl(text)
    if not rows:
        return {"session_id": path.stem, "events": [], "warnings": warnings,
                "format": "empty", "file": path.name}
    first = rows[0]
    if first.get("schema") == SCHEMA:
        sid = first.get("session_id")
        for row in rows:
            if row.get("schema") != SCHEMA or row.get("session_id") != sid:
                raise ValueError("同一文件必须使用相同 schema 和 session_id")
        events = rows
        fmt = SCHEMA
    elif first.get("type") == "session":
        if first.get("version") != 3:
            raise ValueError(f"当前只支持 dsh v3，文件版本为 {first.get('version')!r}")
        sid = first.get("id")
        adapter = DshAdapter(sid)
        events = []
        for event in rows[1:]:
            row = adapter.feed(event)
            if row is not None:
                events.append(row)
        if first.get("isSeeded") or any(e.get("type") == "session/end-seed" for e in rows[1:]):
            warnings.append("此原生日志含继承或恢复边界；当前统计覆盖整个文件，未排除历史事件。")
        fmt = "dsh/session-v3"
    else:
        raise ValueError("不支持的格式：需要 harness-trace/1 或原生 dsh session.v3.jsonl")
    return {"session_id": sid, "events": events, "warnings": warnings, "header": first,
            "format": fmt, "file": path.name}


def summarize(trace: dict) -> dict:
    """Sum only normalized model settlements; null remains distinct from zero."""
    events = trace["events"]
    attempts = [e for e in events if e.get("event") == "model.attempt"]
    totals, coverage = {}, {}
    for field in TOKEN_FIELDS.values():
        values = [e["data"]["usage"].get(field) for e in attempts]
        known = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        totals[field] = sum(known) if known else None
        coverage[field] = len(known)
    return {"turns": len({e.get("turn_id") for e in events if e.get("turn_id") is not None}),
            "steps": len({(e.get("turn_id"), e.get("step_id")) for e in events if e.get("step_id") is not None}),
            "attempts": len(attempts), "tools": sum(e.get("event") == "tool.call" for e in events),
            "tokens": totals, "usage_coverage": coverage}

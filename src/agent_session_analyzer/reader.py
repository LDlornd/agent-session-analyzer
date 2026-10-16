"""Offline readers for harness-trace/1 and uncompressed dsh session v3."""
from __future__ import annotations

import json
from pathlib import Path

from .schema import SCHEMA
from .adapters.dsh import TOKEN_FIELDS, DshAdapter

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


def _label(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        return next((_label(value[key]) for key in ("name", "title", "id")
                     if _label(value.get(key))), "")
    return ""


def _input_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (_input_text(item) for item in value
                         if not isinstance(item, dict) or item.get("role") in (None, "user"))))
    if isinstance(value, dict):
        return _input_text(value.get("content") or value.get("text"))
    return ""


def trace_metadata(trace: dict, path: str | Path) -> dict:
    """Use recorded labels first; infer only the known experiment/trials layout."""
    header = trace.get("header") or {}
    events = trace.get("events", [])
    recording = next((event for event in events if event.get("event") == "session.recording"), {})
    recording_data = recording.get("data") or {}
    if not isinstance(recording_data, dict):
        raise ValueError("session.recording 的 data 必须是 JSON 对象")
    metadata = recording_data.get("metadata") or header.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    title = next((_label(metadata.get(key)) for key in ("task_name", "task", "task_id")
                  if _label(metadata.get(key))), "")
    experiment = next((_label(metadata.get(key)) for key in ("experiment_name", "experiment", "experiment_id")
                       if _label(metadata.get(key))), "")
    # This also works when the selected scan root is inside one trial.
    for parent in Path(path).parents:
        if parent.name == "trials":
            experiment = experiment or parent.parent.name
            break
    models, configured_models, harnesses = set(), set(), set()
    task_input = ""
    for event in events:
        data = event.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("事件 data 必须是 JSON 对象")
        harness = _label(event.get("harness"))
        if harness:
            harnesses.add(harness)
        model = event.get("model") or data.get("model") or {}
        name = _label(model.get("model") if isinstance(model, dict) else model)
        if name:
            configured_models.add(name)
            if event.get("event") == "model.attempt":
                models.add(name)
        if not task_input:
            if event.get("event") in ("run.started", "turn/start"):
                task_input = _input_text(data.get("input"))
            elif event.get("event") == "user/message":
                task_input = _input_text(data.get("content") or data.get("message"))
    if not title and task_input:
        title = next((line.strip().lstrip("# ") for line in task_input.splitlines()
                      if line.strip().lstrip("# ")), "")
        if len(title) > 120:
            title = title[:120] + "…"
    if not harnesses and trace.get("format") == "dsh/session-v3":
        harnesses.add("dsh")
    return {"title": title or "未命名任务", "task_id": _label(metadata.get("task_id")),
            "harness": " / ".join(sorted(harnesses)) or "未知 Harness",
            "models": sorted(models or configured_models) or ["未知模型"],
            "experiment": experiment or "未标注实验"}


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

"""Join optional evaluation artifacts to traces; no harness SDK dependencies."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .reader import MAX_FILE_BYTES, parse_jsonl


def path_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"文件超过 100 MiB：{path}")
    return path.read_text(encoding="utf-8-sig")


def _number(value):
    return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value)) else None


def normalize_trial(row: dict) -> dict:
    """Prefer canonical usage fields; keep the existing OpenHands results readable."""
    agent = row.get("agent") or {}
    usage = agent.get("usage") or {}
    tokens = usage.get("tokens") or {}
    aliases = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens"}
    metrics = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens", "cost_usd"):
        value = usage.get(key)
        if value is None:
            value = tokens.get(key, tokens.get(aliases.get(key)))
        metrics[key] = _number(value)
    return {k: row.get(k) for k in ("trial_id", "task_id", "condition", "repeat", "model",
                                    "status", "passed", "agent_runner", "errors")} | {
        "reward": _number(row.get("reward")), "duration_sec": _number(row.get("duration_sec")),
        "finish_reason": agent.get("finish_reason"), "tool_calls": _number(agent.get("tool_calls")),
        "usage": metrics, "trace_id": None, "trace_error": None,
    }


def load_experiments(paths: list[Path], roots: list[Path]) -> tuple[list[dict], dict[Path, dict], set[Path]]:
    """Read schema_version=1 results; explicit paths are relative to each trial.

    Only this function knows the legacy trials/<id>/agent/events.jsonl layout.
    References outside the selected roots are never read or exposed as traces.
    """
    experiments, contexts, hidden = [], {}, set()
    for result_path in paths:
        if result_path.name != "results.jsonl":
            continue
        directory = result_path.parent
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file() or not any(manifest_path.resolve().is_relative_to(r) for r in roots):
            continue
        experiment = {"id": path_id(directory), "name": directory.name, "directory": str(directory),
                      "status": "unknown", "conditions": [], "planned_trials": None,
                      "trials": [], "warnings": []}
        try:
            manifest = json.loads(_read(manifest_path))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                continue
            config = manifest.get("config") or {}
            if not isinstance(config, dict) or not isinstance(config.get("conditions"), list):
                continue
            if not all(isinstance(c, str) and c for c in config["conditions"]):
                raise ValueError("config.conditions 必须是非空字符串列表")
            hidden.add(result_path)
            experiment.update(name=config.get("name") or directory.name,
                              status=manifest.get("status") or "running",
                              planned_trials=manifest.get("planned_trials"),
                              conditions=list(dict.fromkeys(config["conditions"])))
            rows, warnings = parse_jsonl(_read(result_path))
            experiment["warnings"].extend(warnings)
            for row in rows:
                if row.get("schema_version") != 1 or not all(
                    isinstance(row.get(k), str) and row[k] for k in ("trial_id", "task_id", "condition")
                ) or not isinstance(row.get("repeat"), int) or isinstance(row["repeat"], bool) or row["repeat"] < 1:
                    raise ValueError("trial 需要 schema_version=1、trial_id、task_id、condition 和正整数 repeat")
                trial_dir = (directory / "trials" / row["trial_id"]).resolve()
                if not trial_dir.is_relative_to(directory):
                    raise ValueError("trial_id 不能指向实验目录之外")
                trial = normalize_trial(row)
                trial.update(experiment_id=experiment["id"], experiment_name=directory.name)
                if trial["condition"] not in experiment["conditions"]:
                    experiment["conditions"].append(trial["condition"])
                reference = row.get("trace_path") or (row.get("agent") or {}).get("trace_path")
                legacy = trial_dir / "agent" / "events.jsonl"
                path = (trial_dir / reference).resolve() if reference else legacy.resolve()
                if not any(path.is_relative_to(root) for root in roots):
                    trial["trace_error"] = "trace 位于已选择的扫描目录之外"
                elif not path.is_file():
                    trial["trace_error"] = "trace 文件尚未生成或不存在"
                elif path.suffix != ".jsonl":
                    trial["trace_error"] = "trace_path 必须指向 JSONL 文件"
                elif path in contexts:
                    trial["trace_error"] = "多个 trial 指向同一 trace，无法归属；请每个 trial 单独录制"
                    previous = contexts.pop(path)
                    previous["trace_id"] = None
                    previous["trace_error"] = trial["trace_error"]
                    hidden.add(path)
                elif path in hidden:
                    trial["trace_error"] = "该 trace 已有重复归属"
                else:
                    trial["trace_id"] = path_id(path)
                    contexts[path] = trial
                    if reference and path != legacy.resolve():
                        hidden.add(legacy.resolve())
                experiment["trials"].append(trial)
        except (ValueError, OSError, TypeError, AttributeError) as error:
            experiment["warnings"].append(f"实验结果读取失败：{error}")
        experiments.append(experiment)
    return experiments, contexts, hidden

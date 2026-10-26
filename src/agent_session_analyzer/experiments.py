"""Join optional evaluation artifacts to traces; no harness SDK dependencies."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from .reader import MAX_FILE_BYTES, execution_status, parse_jsonl
from .outcomes import trial_failure_type


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
    durations = {}
    recorded_durations = row.get("stage_durations_sec")
    if isinstance(recorded_durations, dict):
        for stage, value in recorded_durations.items():
            seconds = _number(value)
            durations[stage] = seconds if seconds is not None and seconds >= 0 else None
    return {k: row.get(k) for k in ("trial_id", "task_id", "condition", "repeat", "model",
                                    "status", "passed", "agent_runner", "errors")} | {
        "reward": _number(row.get("reward")), "duration_sec": _number(row.get("duration_sec")),
        "stage_durations_sec": durations,
        "execution_status": execution_status({}, trial=row),
        "failure_type": trial_failure_type(row),
        "finish_reason": agent.get("finish_reason"), "tool_calls": _number(agent.get("tool_calls")),
        "usage": metrics, "trace_id": None, "trace_error": None, "result_recorded": True,
    }


def _in_roots(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _experiment_manifest(directory: Path) -> tuple[dict | None, str | None]:
    path = directory / "manifest.json"
    if not path.is_file():
        return None, None
    try:
        manifest = json.loads(_read(path))
    except (ValueError, OSError) as error:
        # A broken manifest beside trials is an experiment error, not a reason
        # to reinterpret every artifact below it as an independent trace.
        return None, str(error) if (directory / "trials").is_dir() else None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return None, None
    config = manifest.get("config")
    if not isinstance(config, dict) or not isinstance(config.get("conditions"), list):
        return None, None
    if not all(isinstance(condition, str) and condition for condition in config["conditions"]):
        return None, "config.conditions 必须是非空字符串列表"
    return manifest, None


def discover_sources(roots: list[Path]) -> tuple[list[Path], list[dict], dict[Path, dict]]:
    """Find experiment boundaries before discovering their trace files.

    Selecting a trial or trace subdirectory may inspect its enclosing manifest
    for identity. Trace contents and result rows stay inside the selected roots.
    """
    manifests, standalone = {}, set()

    def inspect(directory):
        if directory in manifests:
            return True
        manifest, error = _experiment_manifest(directory)
        if manifest is None and error is None:
            return False
        manifests[directory] = (manifest, error)
        return True

    for root in roots:
        owner = next((parent for parent in (root, *root.parents)
                      if inspect(parent)), None)
        if owner is not None:
            continue
        for current, children, filenames in os.walk(root):
            directory = Path(current)
            if inspect(directory):
                children.clear()  # Experiment artifacts have their own discovery rules.
                continue
            for filename in filenames:
                if filename.endswith(".jsonl"):
                    path = (directory / filename).resolve()
                    if _in_roots(path, roots):
                        standalone.add(path)
    experiments, contexts, ambiguous = load_experiments(manifests, roots)
    paths = sorted((standalone | set(contexts)) - ambiguous)
    return paths, experiments, contexts


def _trial_traces(
    trial_dir: Path, reference, roots: list[Path],
) -> tuple[list[Path], Path | None, str | None]:
    standard = sorted(path.resolve() for path in (trial_dir / "agent" / "trace").glob("*.jsonl")
                      if path.is_file() and _in_roots(path.resolve(), roots))
    legacy = (trial_dir / "agent" / "events.jsonl").resolve()
    if reference:
        if not isinstance(reference, str):
            return standard, None, "trace_path 必须是字符串"
        path = (trial_dir / reference).resolve()
        if not _in_roots(path, roots):
            return standard, None, "trace 位于已选择的扫描目录之外"
        if not path.is_file():
            return standard, None, "trace 文件尚未生成或不存在"
        if path.suffix != ".jsonl":
            return standard, None, "trace_path 必须指向 JSONL 文件"
        if path != legacy or not standard:
            return sorted(set(standard) | {path}), path, None
    if standard:
        if len(standard) == 1:
            return standard, standard[0], None
        return standard, None, "trial 有多个标准 trace，请从会话列表分别打开"
    if not _in_roots(legacy, roots):
        return [], None, "trace 位于已选择的扫描目录之外"
    if not legacy.is_file():
        return [], None, "trace 文件尚未生成或不存在"
    return [legacy], legacy, None


def _validate_trial(row: dict, trial_root: Path) -> Path:
    if row.get("schema_version") != 1 or not all(
        isinstance(row.get(key), str) and row[key] for key in ("trial_id", "task_id", "condition")
    ) or not isinstance(row.get("repeat"), int) or isinstance(row["repeat"], bool) or row["repeat"] < 1:
        raise ValueError("trial 需要 schema_version=1、trial_id、task_id、condition 和正整数 repeat")
    trial_dir = (trial_root / row["trial_id"]).resolve()
    if not trial_dir.is_relative_to(trial_root) or trial_dir == trial_root:
        raise ValueError("trial_id 不能指向 trials 目录之外")
    return trial_dir


def load_experiments(
    manifests: dict[Path, tuple[dict | None, str | None]], roots: list[Path],
) -> tuple[list[dict], dict[Path, dict], set[Path]]:
    """Read manifests and optional results, then inspect each known trial layout."""
    experiments, contexts, ambiguous = [], {}, set()
    for directory, (manifest, manifest_error) in sorted(manifests.items()):
        # Completed experiments may be renamed without changing their artifacts.
        experiment = {"id": path_id(directory), "name": directory.name, "directory": str(directory),
                      "status": "unknown", "conditions": [], "planned_trials": None,
                      "trials": [], "warnings": []}
        try:
            if manifest_error is not None:
                raise ValueError(manifest_error)
            config = manifest["config"]
            experiment.update(status=manifest.get("status") or "running",
                              planned_trials=manifest.get("planned_trials"),
                              conditions=list(dict.fromkeys(config["conditions"])))
            result_path = (directory / "results.jsonl").resolve()
            rows = []
            if result_path.is_file() and _in_roots(result_path, roots):
                try:
                    rows, warnings = parse_jsonl(_read(result_path))
                    experiment["warnings"].extend(warnings)
                except (ValueError, OSError) as error:
                    experiment["warnings"].append(f"实验结果读取失败：{error}")
            else:
                experiment["warnings"].append("扫描范围内尚无 results.jsonl；已有阶段快照和 trace 仍可查看，快照的评分和用量不参与最终结果汇总。")
            trial_root = (directory / "trials").resolve()
            trials, recorded_ids = [], set()
            for row in rows:
                try:
                    trial_dir = _validate_trial(row, trial_root)
                    trial = normalize_trial(row)
                    reference = row.get("trace_path") or (row.get("agent") or {}).get("trace_path")
                    trials.append((trial_dir, trial, reference))
                    recorded_ids.add(row["trial_id"])
                    experiment["trials"].append(trial)
                except (ValueError, TypeError, AttributeError) as error:
                    experiment["warnings"].append(f"trial 结果读取失败：{error}")
            configured_model = config.get("model") or {}
            model_name = configured_model.get("model") if isinstance(configured_model, dict) else None
            trial_directories = sorted(trial_root.iterdir()) if trial_root.is_dir() else []
            for trial_dir in trial_directories:
                if (not trial_dir.is_dir() or trial_dir.is_symlink() or trial_dir.name in recorded_ids
                        or not any(trial_dir.is_relative_to(root) or root.is_relative_to(trial_dir) for root in roots)):
                    continue
                trial = normalize_trial({"trial_id": trial_dir.name, "model": model_name})
                reference = None
                result_path = (trial_dir / "result.json").resolve()
                if result_path.is_file() and _in_roots(result_path, roots):
                    try:
                        row = json.loads(_read(result_path))
                        if not isinstance(row, dict) or _validate_trial(row, trial_root) != trial_dir.resolve():
                            raise ValueError("result.json 的 trial_id 与所在目录不一致")
                        trial = normalize_trial(row)
                        reference = row.get("trace_path") or (row.get("agent") or {}).get("trace_path")
                        experiment["trials"].append(trial)
                    except (ValueError, OSError, TypeError, AttributeError) as error:
                        experiment["warnings"].append(f"{trial_dir.name} 阶段快照读取失败：{error}")
                # Only results.jsonl rows count towards finalized score/usage summaries.
                trial["result_recorded"] = False
                trials.append((trial_dir, trial, reference))
            for trial_dir, trial, reference in trials:
                result_path = (trial_dir / "result.json").resolve()
                if result_path.is_file() and _in_roots(result_path, roots):
                    timestamp_path = result_path
                else:
                    timestamp_path = trial_dir if trial_dir.is_dir() else directory
                trial.update(experiment_id=experiment["id"], experiment_name=experiment["name"],
                             directory=str(trial_dir.resolve()),
                             modified_ms=int(timestamp_path.stat().st_mtime * 1000))
                if trial["condition"] and trial["condition"] not in experiment["conditions"]:
                    experiment["conditions"].append(trial["condition"])
                paths, primary, error = _trial_traces(trial_dir, reference, roots)
                trial.update(trace_id=path_id(primary) if primary else None, trace_error=error)
                for path in paths:
                    if path in contexts:
                        previous = contexts.pop(path)
                        previous.update(trace_id=None, trace_error="多个 trial 指向同一 trace，无法归属")
                        ambiguous.add(path)
                    if path in ambiguous:
                        trial.update(trace_id=None, trace_error="该 trace 已有重复归属")
                    else:
                        contexts[path] = trial
        except (ValueError, OSError, TypeError, AttributeError) as error:
            experiment["warnings"].append(f"实验读取失败：{error}")
        experiments.append(experiment)
    return experiments, contexts, ambiguous

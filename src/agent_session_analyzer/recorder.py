"""Synchronous SDK wrapper: no monkey patches, background agents, or model calls."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .adapter import SCHEMA, DshAdapter


class DshRecorder:
    """One JSONL per observed session per recorder lifetime, including child sessions.

    Every row is flushed. Run boundaries additionally fsync. A new recorder uses
    new filenames, so separate processes never append to the same file.
    Recording errors propagate instead of silently producing incomplete metrics.
    """

    def __init__(self, log_dir: str | Path = "logs/harness", *, metadata: dict | None = None):
        self.log_dir = Path(log_dir).resolve()
        self.metadata = metadata or {}
        self.recording_id = uuid.uuid4().hex
        self.paths: dict[str, Path] = {}
        self._files = {}
        self._adapters = {}
        self._lock = threading.RLock()
        self._running = False
        self._closed = False
        self.last_path: Path | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _write(self, sid, row, *, sync=False):
        row = {**row, "recording_id": self.recording_id, "observed_at_ms": time.time_ns() // 1_000_000}
        line = json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        handle = self._files[sid]
        handle.write(line + "\n")
        handle.flush()
        if sync:
            os.fsync(handle.fileno())

    def _session(self, sid, root_id, model):
        if sid in self._files:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        slug = hashlib.sha256(sid.encode()).hexdigest()[:16]
        path = self.log_dir / f"{slug}-{self.recording_id}.jsonl"
        self._files[sid] = path.open("x", encoding="utf-8", newline="\n")
        self.paths[sid] = path
        self._adapters[sid] = DshAdapter(sid, model if sid == root_id else {})
        try:
            sdk_version = version("deepseek-harness-sdk")
        except PackageNotFoundError:
            sdk_version = None
        self._write(sid, {"schema": SCHEMA, "event": "session.recording", "harness": "dsh",
                         "session_id": sid, "root_session_id": root_id,
                         "time_ms": time.time_ns() // 1_000_000,
                         "data": {"sdk_version": sdk_version, "metadata": self.metadata,
                                  "model": model if sid == root_id else {},
                                  "capture": "SDK session.event notifications; settled model streams",
                                  "reasoning": "only content exposed by the provider",
                                  "api_attempt_coverage": "dsh-visible attempts; hidden transport retries unavailable"}}, sync=True)

    def run(self, harness, input, *, session_id=None, on_notification=None):
        """Drop-in equivalent of harness.run(input, ...), returning its RunResult.

        Reuse session_id for follow-ups. One recorder supports serial runs; use
        separate recorder instances for parallel runs. Existing callbacks remain active.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Recorder is closed")
            if self._running:
                raise RuntimeError("Use one DshRecorder per concurrent run")
            self._running = True
        sid = session_id or f"session-{uuid.uuid4().hex}"
        run_id = uuid.uuid4().hex
        config = getattr(harness, "config", None)
        model = {k: getattr(config, k) for k in ("provider", "model") if getattr(config, k, None) is not None}
        touched = set()
        try:
            with self._lock:
                self._session(sid, sid, model)
                self.last_path = self.paths[sid]
                self._write(sid, {"schema": SCHEMA, "event": "run.started", "session_id": sid,
                                 "run_id": run_id, "time_ms": time.time_ns() // 1_000_000,
                                 "data": {"input": input, "model": model,
                                          "cwd": getattr(config, "cwd", None)}}, sync=True)
            touched.add(sid)

            def collect(notification):
                if notification.method == "session.event":
                    payload = notification.payload
                    event_sid, event = payload.get("sessionId"), payload.get("event")
                    if not isinstance(event_sid, str) or not isinstance(event, dict):
                        raise ValueError("Malformed SDK session.event notification")
                    with self._lock:
                        self._session(event_sid, sid, model)
                        touched.add(event_sid)
                        row = self._adapters[event_sid].feed(event)
                        if row is not None:
                            self._write(event_sid, {**row, "run_id": run_id})
                if on_notification is not None:
                    on_notification(notification)

            result = harness.run(input, session_id=sid, on_notification=collect)
            with self._lock:
                self._write(sid, {"schema": SCHEMA, "event": "run.finished", "session_id": sid,
                                 "run_id": run_id, "time_ms": time.time_ns() // 1_000_000,
                                 "data": {"finish_reason": result.finish_reason,
                                          "final_response": result.final_response}}, sync=True)
            return result
        except BaseException as error:
            if sid in self._files:
                try:
                    self._write(sid, {"schema": SCHEMA, "event": "run.failed", "session_id": sid,
                                     "run_id": run_id, "time_ms": time.time_ns() // 1_000_000,
                                     "data": {"error_type": type(error).__name__, "message": str(error)}}, sync=True)
                except Exception:
                    pass  # Preserve the original failure, including a disk-write error.
            raise
        finally:
            try:
                for event_sid in touched:
                    self._files[event_sid].flush()
                    os.fsync(self._files[event_sid].fileno())
            finally:
                with self._lock:
                    self._running = False

    def close(self):
        with self._lock:
            if self._running:
                raise RuntimeError("Cannot close recorder during an active run")
            errors = []
            for handle in self._files.values():
                if not handle.closed:
                    try:
                        handle.flush()
                        os.fsync(handle.fileno())
                    except OSError as error:
                        errors.append(error)
                    finally:
                        handle.close()
            self._closed = True
            if errors:
                raise ExceptionGroup("Unable to flush trace files", errors)

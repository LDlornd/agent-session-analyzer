"""Local synchronous OpenHands recorder using callbacks, without SDK monkey patches."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..adapters.openhands import OpenHandsAdapter, as_dict, now_ms


class OpenHandsRecorder:
    """Own log files, not conversations. Close conversations separately.

    Use create_conversation() then run(). One recorder allows serial runs only;
    each run temporarily owns telemetry/retry hooks on the agent's current LLMs.
    No async, remote Conversation, or concurrently shared LLMs are supported.
    """

    def __init__(self, log_dir="logs/harness", *, metadata=None):
        self.log_dir = Path(log_dir).resolve()
        self.metadata = metadata or {}
        self.recording_id = uuid.uuid4().hex
        self.paths = {}
        self.last_path = None
        self._bindings = {}
        self._lock = threading.RLock()
        self._running = False
        self._closed = False
        self._capture_error = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _check(self):
        if self._closed:
            raise RuntimeError("Recorder is closed")
        if self._capture_error is not None:
            raise RuntimeError("OpenHands trace capture failed") from self._capture_error

    def _write(self, binding, rows, *, sync=False):
        with self._lock:
            self._check()
            try:
                for row in rows:
                    line = {**row, "recording_id": self.recording_id,
                            "run_id": binding.get("run_id"), "observed_at_ms": now_ms()}
                    binding["file"].write(json.dumps(line, ensure_ascii=False, allow_nan=False) + "\n")
                binding["file"].flush()
                if sync:
                    os.fsync(binding["file"].fileno())
            except Exception as error:
                self._capture_error = error
                raise

    def _capture(self, binding, method, *args, **kwargs):
        # Telemetry catches callback exceptions. Latch them and fail run() even
        # when the SDK suppresses the exception, so missing metrics are not hidden.
        with self._lock:
            self._check()
            try:
                self._write(binding, method(*args, **kwargs))
            except Exception as error:
                self._capture_error = error
                raise

    def create_conversation(self, *, agent, workspace=".", callbacks=None, **kwargs):
        with self._lock:
            self._check()
            if self._running:
                raise RuntimeError("Cannot create a conversation during run()")
        if not isinstance(workspace, (str, Path)):
            raise TypeError("This recorder supports local workspace paths only")
        # Optional dependency: DshRecorder remains importable without OpenHands.
        from openhands.sdk import Conversation

        sid_value = kwargs.pop("conversation_id", None) or uuid.uuid4()
        sid_value = uuid.UUID(str(sid_value))
        sid = str(sid_value)
        if sid in self._bindings:
            raise ValueError("Conversation is already attached to this recorder")
        llm = getattr(agent, "llm", None)
        model = {"model": getattr(llm, "model", None)}
        adapter = OpenHandsAdapter(sid, model)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{hashlib.sha256(sid.encode()).hexdigest()[:16]}-{self.recording_id}.jsonl"
        binding = {"adapter": adapter, "file": path.open("x", encoding="utf-8", newline="\n"), "run_id": None}
        try:
            try:
                sdk_version = version("openhands-sdk")
            except PackageNotFoundError:
                sdk_version = None
            self._write(binding, [adapter.row("session.recording", {
                "sdk_version": sdk_version, "metadata": self.metadata, "model": model,
                "capture": "local synchronous Conversation callbacks + LLM telemetry/retry callbacks",
                "step_semantics": "derived response group; request-start timestamp unavailable",
                "turn_semantics": "one recorder.run invocation, including resume",
                "limitations": "No hidden provider retries, async/remote runs, or dynamic LLM replacement coverage guarantee",
            })], sync=True)

            def on_event(event):
                self._capture(binding, adapter.event, event)

            conversation = Conversation(agent=agent, workspace=workspace,
                conversation_id=sid_value, callbacks=[on_event, *(callbacks or [])], **kwargs)
            binding["conversation"] = conversation
            self._bindings[sid] = binding
            self.paths[sid] = path
            self.last_path = path
            return conversation
        except BaseException:
            binding["file"].close()
            raise

    def _install_hooks(self, conversation, binding, restores):
        seen_telemetry = set()
        for index, llm in enumerate(conversation.agent.get_all_llms()):
            telemetry = llm.telemetry
            if id(telemetry) in seen_telemetry:
                raise RuntimeError("LLMs share telemetry; cannot attribute attempts reliably")
            seen_telemetry.add(id(telemetry))
            key = f"{getattr(llm, 'usage_id', 'llm')}:{index}"
            model = {"model": llm.model, "usage_id": key}
            # OpenHands exposes a setter but no getter. Read this one private
            # slot to preserve/restore an existing callback; fail if it drifts.
            old_callback = telemetry._log_completions_callback
            old_retry = llm.retry_listener
            old_enabled = telemetry.log_enabled
            old_log_flag = llm.log_completions
            restores.append((llm, telemetry, old_callback, old_retry, old_enabled, old_log_flag))

            def completion(filename, log_data, *, key=key, model=model, previous=old_callback):
                self._capture(binding, binding["adapter"].completion, filename, log_data, llm_key=key, model=model)
                if previous:
                    previous(filename, log_data)

            def retry(attempt, maximum, error, *, key=key, model=model, previous=old_retry):
                self._capture(binding, binding["adapter"].retry, key, attempt, maximum, error, model=model)
                if previous:
                    previous(attempt, maximum, error)

            llm.log_completions = True
            telemetry.log_enabled = True
            telemetry.set_log_completions_callback(completion)
            llm.retry_listener = retry

    def run(self, conversation, task=None):
        """Send task (unless None), run synchronously, and return native run() value.

        task=None resumes pending work. A returned run may be paused, waiting for
        confirmation, stuck or failed; only SDK status 'finished' means completed.
        """
        with self._lock:
            self._check()
            if self._running:
                raise RuntimeError("Use a separate recorder per concurrent run")
            binding = self._bindings.get(str(conversation.id))
            if binding is None or binding["conversation"] is not conversation:
                raise ValueError("Create this conversation with recorder.create_conversation()")
            if task is None and binding["adapter"].turn == 0:
                raise ValueError("The first run requires a task; task=None is for resuming later runs")
            self._running = True
        adapter = binding["adapter"]
        binding["run_id"] = uuid.uuid4().hex
        self.last_path = self.paths[str(conversation.id)]
        restores = []
        try:
            payload = task if task is None or isinstance(task, str) else as_dict(task)
            self._write(binding, [adapter.row("run.started", {"input": payload})], sync=True)
            self._write(binding, adapter.begin_turn(payload))
            if task is not None:
                conversation.send_message(task)
            # send_message initializes/registers the agent's LLMs in SDK 1.49.1.
            # Hook actual registered objects after that initialization, which
            # can reset telemetry or create condenser LLMs.
            self._install_hooks(conversation, binding, restores)
            result = conversation.run()
            self._check()
            status = conversation.state.execution_status
            status = getattr(status, "value", status)
            status = "completed" if status == "finished" else str(status)
            self._write(binding, adapter.end_turn(status))
            self._write(binding, [adapter.row("run.finished", {"finish_reason": status})], sync=True)
            return result
        except BaseException as error:
            if self._capture_error is None:
                self._write(binding, adapter.end_turn("error"))
                self._write(binding, [adapter.row("run.failed", {
                    "error_type": type(error).__name__, "message": str(error)})], sync=True)
            raise
        finally:
            try:
                for llm, telemetry, callback, retry, enabled, log_flag in reversed(restores):
                    telemetry.set_log_completions_callback(callback)
                    telemetry.log_enabled = enabled
                    llm.log_completions = log_flag
                    llm.retry_listener = retry
            finally:
                binding["run_id"] = None
                with self._lock:
                    self._running = False

    def close(self):
        with self._lock:
            if self._running:
                raise RuntimeError("Cannot close recorder during run()")
            errors = []
            for binding in self._bindings.values():
                handle = binding["file"]
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
                raise ExceptionGroup("Unable to flush OpenHands logs", errors)

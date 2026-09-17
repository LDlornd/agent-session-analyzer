"""Offline contract tests; no SDK, API key or network required."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_session_analyzer import OpenHandsAdapter, OpenHandsRecorder


def completion(response_id="r1"):
    return {"response": {"id": response_id, "model": "test-model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                  "prompt_tokens_details": {"cached_tokens": 0}},
        "choices": [{"message": {"content": "done", "reasoning_content": "visible reasoning"},
                     "finish_reason": "stop"}]}}


class AdapterTests(unittest.TestCase):
    def test_multiple_tools_share_one_attempt(self):
        adapter = OpenHandsAdapter("session")
        rows = adapter.begin_turn("task")
        rows += adapter.completion("one.json", completion())
        for cid in ("a", "b"):
            rows += adapter.event({"kind": "ActionEvent", "id": cid,
                "llm_response_id": "r1", "tool_call_id": cid, "tool_name": "test",
                "action": {}, "thought": [{"type": "text", "text": "comment"}]})
        for cid in ("b", "a"):
            rows += adapter.event({"kind": "ObservationEvent", "id": "result-" + cid,
                "tool_call_id": cid, "observation": {"content": cid}})
        self.assertEqual(adapter.completion("duplicate.json", completion()), [])
        rows += adapter.end_turn("completed")
        attempts = [r for r in rows if r["event"] == "model.attempt"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["data"]["usage"]["total_tokens"], 14)
        self.assertEqual(attempts[0]["data"]["usage"]["cache_read_tokens"], 0)
        self.assertIsNone(attempts[0]["data"]["usage"]["reasoning_tokens"])
        results = [r for r in rows if r["event"] == "tool.result"]
        self.assertEqual([r["tool_call_id"] for r in results], ["b", "a"])
        self.assertTrue(all(r["attempt_id"] == attempts[0]["attempt_id"] for r in results))

    def test_retries_and_missing_completion(self):
        adapter = OpenHandsAdapter("session")
        adapter.begin_turn("task")
        rows = adapter.retry("main", 1, 3, RuntimeError("retry"))
        rows += adapter.retry("main", 2, 3, RuntimeError("retry"))
        rows += adapter.completion("done.json", completion())
        attempts = [r for r in rows if r["event"] == "model.attempt"]
        self.assertEqual([r["data"]["attempt_index"] for r in attempts], [1, 2, 3])
        self.assertEqual(len({r["step_id"] for r in attempts}), 1)
        self.assertIsNone(attempts[0]["data"]["usage"]["input_tokens"])
        adapter.end_turn("completed")
        adapter.begin_turn("next")
        adapter.event({"kind": "MessageEvent", "id": "message", "source": "agent",
                       "llm_response_id": "missing", "llm_message": {"content": "answer"}})
        rows = adapter.end_turn("completed")
        self.assertEqual(rows[0]["data"]["status"], "event_only")
        self.assertTrue(any(r["event"] == "capture.warning" for r in rows))

    def test_responses_reasoning(self):
        adapter = OpenHandsAdapter("session")
        adapter.begin_turn("task")
        rows = adapter.completion("response.json", {"response": {"id": "r",
            "output": [{"type": "reasoning", "summary": [{"text": "summary"}]}]}})
        self.assertEqual(rows[-1]["data"]["reasoning"], "summary")
        self.assertIsNone(rows[-1]["data"]["usage"]["total_tokens"])


class FakeTelemetry:
    def __init__(self):
        self._log_completions_callback = None
        self.log_enabled = False

    def set_log_completions_callback(self, callback):
        self._log_completions_callback = callback


class FakeConversation:
    def __init__(self, *, agent, workspace, conversation_id, callbacks):
        self.agent, self.id, self.callbacks = agent, conversation_id, callbacks
        self.state = types.SimpleNamespace(execution_status="paused")
        self.count = 0
        self.fail = False

    def send_message(self, task):
        for callback in self.callbacks:
            callback({"kind": "MessageEvent", "source": "user", "llm_message": {"content": task}})

    def run(self):
        if self.fail:
            raise RuntimeError("native failure")
        self.count += 1
        self.agent.llm.telemetry._log_completions_callback(
            str(self.count), json.dumps(completion(str(self.count))))


class RecorderTests(unittest.TestCase):
    def test_turns_status_and_hook_restoration(self):
        sdk = types.ModuleType("openhands.sdk")
        sdk.Conversation = FakeConversation
        previous = []
        telemetry = FakeTelemetry()
        callback = lambda *args: previous.append(args)
        telemetry.set_log_completions_callback(callback)
        llm = types.SimpleNamespace(telemetry=telemetry, model="test", usage_id="main",
                                    retry_listener=None, log_completions=False)
        agent = types.SimpleNamespace(llm=llm, get_all_llms=lambda: [llm])
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"openhands.sdk": sdk}):
            with OpenHandsRecorder(directory) as recorder:
                conversation = recorder.create_conversation(agent=agent)
                with self.assertRaisesRegex(ValueError, "first run"):
                    recorder.run(conversation)
                recorder.run(conversation, "first")
                recorder.run(conversation, "second")
                self.assertIs(telemetry._log_completions_callback, callback)
                self.assertFalse(telemetry.log_enabled)
                self.assertFalse(llm.log_completions)
                self.assertEqual(len(previous), 2)
                conversation.fail = True
                with self.assertRaisesRegex(RuntimeError, "native failure"):
                    recorder.run(conversation)
                self.assertIs(telemetry._log_completions_callback, callback)
                path = recorder.last_path
            rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
            turns = [r for r in rows if r["event"] == "turn/end"]
            self.assertEqual([r["data"]["reason"]["kind"] for r in turns], ["paused", "paused", "error"])
            self.assertEqual([r["turn_id"] for r in turns], [1, 2, 3])
            self.assertTrue(any(r["event"] == "run.failed" for r in rows))


if __name__ == "__main__":
    unittest.main()

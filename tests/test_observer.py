"""Offline verification only: no harness subprocess, tools, or model requests."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

from agent_session_analyzer.adapters.dsh import DshAdapter
from agent_session_analyzer.reader import parse_jsonl, read_trace, summarize
from agent_session_analyzer.recorders.dsh import DshRecorder
from agent_session_analyzer.viewer import TraceServer


def event(kind, seq, **data):
    return {"type": kind, "seq": seq, "time": 1000 + seq * 10, "data": data}


def example(turn=1, offset=0):
    return [event("turn/start", offset, turn=turn),
            event("step/start", offset+1, turn=turn, step=1),
            event("request/context", offset+2, provider="test", model="test-model"),
            event("assistant/attempt", offset+3, turn=turn, step=1, stream=[
                {"type": "reasoning-chunks", "time0": 1000, "dt": [1], "texts": ["失败前的思考"]},
                {"type": "chunk", "time": 1002, "chunk": {"type": "finish", "reason": {"kind": "error"}}}]),
            event("assistant/message", offset+4, turn=turn, step=1,
                  message={"content": [{"type": "reasoning", "text": "检查工具"}, {"type": "text", "text": "准备执行"}]},
                  usage={"inputTokens": 10, "outputTokens": 5, "cacheReadTokens": 20, "totalTokens": 35, "reasoningTokens": 2},
                  stream=[{"type": "chunk", "time": 1003, "chunk": {"type": "usage", "usage": {"totalTokens": 35}}}]),
            event("tool/call", offset+5, turn=turn, step=1, callId="a", name="read", arguments='{"path":"中文.txt"}'),
            event("tool/call", offset+6, turn=turn, step=1, callId="b", name="search", arguments="{}"),
            event("tool/result", offset+7, turn=turn, step=1, message={"content": [
                {"type": "tool-result", "toolCallId": "b", "isError": True, "content": "failed"}]}),
            event("tool/result", offset+8, turn=turn, step=1, message={"content": [
                {"type": "tool-result", "toolCallId": "a", "isError": False, "content": "文件内容"}]}),
            event("step/end", offset+9, turn=turn, step=1),
            event("turn/end", offset+10, turn=turn, reason={"kind": "completed"})]


class FakeHarness:
    config = SimpleNamespace(model="configured", provider="test", cwd="example", api_key="DO_NOT_RECORD")

    def __init__(self):
        self.runs = 0

    def run(self, input, *, session_id, on_notification):
        self.runs += 1
        rows = example(self.runs, (self.runs-1)*11)
        for row in rows + [rows[-1]]:
            on_notification(SimpleNamespace(method="session.event", payload={"sessionId": session_id, "event": row}))
        return SimpleNamespace(session_id=session_id, finish_reason="completed", final_response="完成", events=rows)


class ObserverTest(unittest.TestCase):
    def test_retries_tools_and_usage(self):
        adapter = DshAdapter("s")
        rows = [adapter.feed(e) for e in example()]
        trace = {"events": rows}
        stats = summarize(trace)
        self.assertEqual((stats["turns"], stats["steps"], stats["attempts"], stats["tools"]), (1, 1, 2, 2))
        self.assertEqual(stats["tokens"]["total_tokens"], 35)
        self.assertEqual(stats["usage_coverage"]["total_tokens"], 1)
        attempts = [r for r in rows if r["event"] == "model.attempt"]
        self.assertEqual(attempts[0]["data"]["reasoning"], "失败前的思考")
        self.assertEqual(attempts[1]["data"]["reasoning"], "检查工具")
        self.assertEqual(rows[5]["attempt_id"], "1/1/2")
        self.assertEqual(rows[7]["tool_call_id"], "b")
        self.assertTrue(rows[7]["data"]["is_error"])
        self.assertIsNone(adapter.feed(example()[-1]))
        with self.assertRaises(ValueError):
            adapter.feed(event("turn/start", 0, turn=999))

    def test_wrapper_multiple_turns_callback_and_no_config_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            seen=[]
            with DshRecorder(folder) as recorder:
                harness=FakeHarness()
                for _ in range(2):
                    result=recorder.run(harness, "任务", session_id="../../session", on_notification=seen.append)
                    self.assertEqual(result.final_response,"完成")
                self.assertEqual(len(recorder.paths),1)
            text=recorder.last_path.read_text(encoding="utf-8")
            self.assertNotIn("DO_NOT_RECORD",text)
            self.assertEqual(len(seen),24)
            trace=read_trace(recorder.last_path)
            self.assertEqual(summarize(trace)["turns"],2)
            self.assertEqual(summarize(trace)["tokens"]["total_tokens"],70)
            self.assertEqual(recorder.last_path.parent,Path(folder).resolve())

    def test_failure_and_child_sessions(self):
        class Failing(FakeHarness):
            def run(self, input, *, session_id, on_notification):
                for sid in [session_id,"child"]:
                    on_notification(SimpleNamespace(method="session.event",payload={"sessionId":sid,"event":event("turn/start",0,turn=1)}))
                raise RuntimeError("test failure")
        with tempfile.TemporaryDirectory() as folder:
            with DshRecorder(folder) as recorder:
                with self.assertRaisesRegex(RuntimeError,"test failure"):
                    recorder.run(Failing(),"test",session_id="root")
            self.assertEqual(len(recorder.paths),2)
            trace=read_trace(recorder.paths["root"])
            self.assertEqual(trace["events"][-1]["event"],"run.failed")
            child=read_trace(recorder.paths["child"])
            self.assertEqual(child["events"][0]["root_session_id"],"root")
            self.assertEqual(child["events"][1]["model"],{})

    def test_native_v3_and_partial_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"session.v3.jsonl"
            rows=[{"type":"session","version":3,"id":"s"},*example()]
            path.write_text("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n{"incomplete":',encoding="utf-8")
            trace=read_trace(path)
            self.assertEqual(summarize(trace)["attempts"],2)
            self.assertEqual(len(trace["warnings"]),1)
        with self.assertRaises(ValueError):
            parse_jsonl('{broken}\n{}\n')

    def test_viewer_serves_only_catalogued_files(self):
        with tempfile.TemporaryDirectory() as folder:
            with DshRecorder(folder) as recorder:
                recorder.run(FakeHarness(),"test")
            server=TraceServer(("127.0.0.1",0),[folder])
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base+"/api/files") as r: files=json.load(r)["files"]
                with urlopen(base+"/api/trace?id="+files[0]["id"]) as r: trace=json.load(r)
                self.assertEqual(trace["summary"]["tools"],2)
                with urlopen(base+"/") as r: self.assertIn(b"Harness Trace",r.read())
                with self.assertRaises(HTTPError):urlopen(base+"/api/trace?id=../../secret")
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__ == "__main__":
    unittest.main()

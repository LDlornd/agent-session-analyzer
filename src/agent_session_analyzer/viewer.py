"""Loopback-only, read-only viewer. Python standard library; no uploads/CDNs."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .experiments import discover_sources, path_id
from .reader import UnsupportedTraceFormat, read_trace, summarize, trace_metadata


def choose_directory() -> str:
    # Tk must run on its own main thread, outside the HTTP request threads.
    script = """
import json
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
try:
    path = filedialog.askdirectory(parent=root, title="选择 JSONL 扫描目录", mustexist=True)
    print(json.dumps(path))
finally:
    root.destroy()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode:
        raise OSError("无法打开系统目录窗口：请使用带 tkinter 和图形桌面的 Python。\n" + result.stderr.strip())
    return json.loads(result.stdout)


class TraceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, roots):
        self.roots = [Path(p).resolve() for p in roots]
        super().__init__(address, TraceHandler)

    def inventory(self, roots):
        paths, experiments, contexts = discover_sources(roots)
        entries = {}
        for path in paths:
            root = next(root for root in roots if path.is_relative_to(root))
            stat = path.stat()
            key = path_id(path)
            trial = contexts.get(path)
            entries[key] = {"id": key, "name": str(path.relative_to(root)),
                            "root": str(root), "size": stat.st_size,
                            "modified_ms": int(stat.st_mtime * 1000), "path": path,
                            "trace_root": trial["directory"] if trial else str(path.parent),
                            "trial": trial}
        return entries, experiments


class TraceHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, body, content_type="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def requested_roots(self, query):
        values = parse_qs(query, keep_blank_values=True).get("root")
        if values is None:
            return self.server.roots
        roots = []
        for value in values:
            value = value.strip()
            if not value:
                continue
            root = Path(value).expanduser().resolve()
            if not root.is_dir():
                raise ValueError(f"扫描目录不存在或不是目录：{root}")
            if root not in roots:
                roots.append(root)
        return roots

    def do_POST(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if (host.split(":", 1)[0] not in ("127.0.0.1", "localhost")
                or (origin is not None and origin != f"http://{host}")
                or self.headers.get("X-Trace-Viewer") != "1"):
            self.send(403, {"error": "Only same-origin loopback access is allowed"})
            return
        if self.path != "/api/choose-directory":
            self.send(404, {"error": "Not found"})
            return
        try:
            self.send(200, {"path": choose_directory()})
        except (ValueError, OSError) as error:
            self.send(400, {"error": str(error)})

    def do_GET(self):
        host = self.headers.get("Host", "").split(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            self.send(403, {"error": "Only loopback access is allowed"})
            return
        url = urlparse(self.path)
        try:
            assets = {"/": ("viewer.html", "text/html; charset=utf-8"),
                      "/viewer.js": ("viewer.js", "text/javascript; charset=utf-8"),
                      "/viewer.css": ("viewer.css", "text/css; charset=utf-8")}
            if url.path in assets:
                name, mime = assets[url.path]
                self.send(200, Path(__file__).with_name(name).read_bytes(), mime)
            elif url.path == "/api/files":
                roots = self.requested_roots(url.query)
                entries, experiments = self.server.inventory(roots)
                files = []
                for item in entries.values():
                    entry = {k: v for k, v in item.items() if k != "path"}
                    try:
                        trace = read_trace(item["path"], trial=item["trial"])
                        entry.update(trace_metadata(trace, trial=item["trial"]))
                    except UnsupportedTraceFormat:
                        continue  # results/tool-call logs are not session traces.
                    except (ValueError, OSError, TypeError, AttributeError, KeyError) as error:
                        entry.update(trace_metadata({}, trial=item["trial"]), error=str(error))
                    files.append(entry)
                self.send(200, {"files": sorted(files, key=lambda x: x["modified_ms"], reverse=True),
                                "roots": [str(p) for p in roots], "experiments": experiments})
            elif url.path == "/api/trace":
                key = parse_qs(url.query).get("id", [""])[0]
                entries, _ = self.server.inventory(self.requested_roots(url.query))
                entry = entries.get(key)
                if entry is None:
                    self.send(404, {"error": "文件不存在，或不在配置的目录中"})
                    return
                trace = read_trace(entry["path"], trial=entry["trial"])
                trace["summary"] = summarize(trace)
                trace["metadata"] = trace_metadata(trace, trial=entry["trial"])
                trace["trial"] = entry["trial"]
                self.send(200, trace)
            else:
                self.send(404, {"error": "Not found"})
        except (ValueError, OSError, TypeError, AttributeError, KeyError) as error:
            self.send(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description="本地 harness JSONL 查看器")
    parser.add_argument("--root", action="append", help="日志目录，可重复传入；未指定时在网页中点击选择")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    roots = args.root or []
    server = TraceServer(("127.0.0.1", args.port), roots)
    print(f"JSONL viewer: http://127.0.0.1:{server.server_port}", flush=True)
    for root in server.roots:
        print(f"  {root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

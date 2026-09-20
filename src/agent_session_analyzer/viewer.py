"""Loopback-only, read-only viewer. Python standard library; no uploads/CDNs."""
from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .reader import read_trace, summarize


class TraceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, roots):
        self.roots = [Path(p).resolve() for p in roots]
        super().__init__(address, TraceHandler)

    def catalog(self, roots=None):
        entries = {}
        for root in (self.roots if roots is None else roots):
            if not root.is_dir():
                continue
            for path in root.rglob("*.jsonl"):
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    continue
                stat = resolved.stat()
                key = hashlib.sha256(str(resolved).encode()).hexdigest()
                entries[key] = {"id": key, "name": str(resolved.relative_to(root)),
                                "root": str(root), "size": stat.st_size,
                                "modified_ms": int(stat.st_mtime * 1000), "path": resolved}
        return entries


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
                entries = self.server.catalog(roots)
                files = [{k: v for k, v in item.items() if k != "path"} for item in entries.values()]
                self.send(200, {"files": sorted(files, key=lambda x: x["modified_ms"], reverse=True),
                                "roots": [str(p) for p in roots]})
            elif url.path == "/api/trace":
                key = parse_qs(url.query).get("id", [""])[0]
                entry = self.server.catalog(self.requested_roots(url.query)).get(key)
                if entry is None:
                    self.send(404, {"error": "文件不存在，或不在配置的目录中"})
                    return
                trace = read_trace(entry["path"])
                trace["summary"] = summarize(trace)
                self.send(200, trace)
            else:
                self.send(404, {"error": "Not found"})
        except (ValueError, OSError) as error:
            self.send(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description="本地 harness JSONL 查看器")
    parser.add_argument("--root", action="append", help="预填日志目录，可重复传入；未指定时在网页中填写")
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

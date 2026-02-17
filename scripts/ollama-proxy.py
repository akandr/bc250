#!/usr/bin/env python3
"""
Lightweight reverse proxy for Ollama that injects "think": false into /api/chat requests.

This fixes qwen3 models defaulting to thinking mode when tools are present,
which causes all tokens to go to the "thinking" field with empty "content".

Usage:
  python3 ollama-proxy.py [--port 11435] [--upstream http://127.0.0.1:11434]

OpenClaw config should point baseUrl to http://127.0.0.1:11435
"""

import http.server
import urllib.request
import urllib.error
import json
import sys
import argparse

UPSTREAM = "http://127.0.0.1:11434"
PORT = 11435


class OllamaProxy(http.server.BaseHTTPRequestHandler):

    def _proxy(self, method="GET", body=None):
        url = f"{UPSTREAM}{self.path}"
        req = urllib.request.Request(url, data=body, method=method)
        # Copy headers
        for h in ("Content-Type", "Accept", "Authorization"):
            val = self.headers.get(h)
            if val:
                req.add_header(h, val)
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
            return
        resp_body = resp.read()

        # Log /api/chat responses
        if self.path == "/api/chat":
            try:
                lines = resp_body.decode("utf-8", errors="replace").strip().split("\n")
                print(f"[proxy] Response: {len(lines)} chunks, total {len(resp_body)} bytes", file=sys.stderr, flush=True)
                # For streaming, each line is a JSON chunk
                for i, line in enumerate(lines):
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})
                    c = msg.get("content", "")
                    t = msg.get("thinking", "")
                    tc = msg.get("tool_calls")
                    if c:
                        print(f"[proxy] CONTENT[{i}]: {c[:200]}", file=sys.stderr, flush=True)
                    if t:
                        print(f"[proxy] THINKING[{i}]: {t[:200]}", file=sys.stderr, flush=True)
                    if tc:
                        print(f"[proxy] TOOL_CALLS[{i}]: {json.dumps(tc)[:300]}", file=sys.stderr, flush=True)
                    if chunk.get("done"):
                        print(f"[proxy] DONE eval_count={chunk.get('eval_count', 0)} done_reason={chunk.get('done_reason', '')}", file=sys.stderr, flush=True)
                        # Dump last chunk
                        print(f"[proxy] FINAL_MSG: {json.dumps(msg)[:500]}", file=sys.stderr, flush=True)
                # Log first 5 raw chunks
                for i, line in enumerate(lines[:5]):
                    print(f"[proxy] RAW[{i}]: {line[:300]}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[proxy] log error: {e}", file=sys.stderr, flush=True)

        self.send_response(resp.status)
        for h, v in resp.getheaders():
            if h.lower() not in ("transfer-encoding",):
                self.send_header(h, v)
        self.end_headers()
        self.wfile.write(resp_body)

    def do_GET(self):
        self._proxy("GET")

    def do_HEAD(self):
        self._proxy("HEAD")

    def do_DELETE(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        self._proxy("DELETE", body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        # Inject think:false for /api/chat requests with tools (qwen3 only)
        if self.path == "/api/chat" and body:
            try:
                data = json.loads(body)
                model = data.get("model", "")
                # Only inject think:false for vanilla qwen3 (not nothink/abliterated variants)
                # qwen3-abl-nothink NEEDS thinking to produce tool_calls with large prompts
                needs_think_false = (
                    data.get("tools")
                    and "think" not in data
                    and "qwen3" in model
                    and "nothink" not in model
                    and "abliterated" not in model
                )
                if needs_think_false:
                    data["think"] = False
                    body = json.dumps(data).encode("utf-8")
                    print(
                        f"[proxy] Injected think:false for model={model} "
                        f"(tools={len(data.get('tools', []))})",
                        file=sys.stderr,
                        flush=True,
                    )
                elif data.get("tools"):
                    print(
                        f"[proxy] Passthrough for model={model} "
                        f"(tools={len(data.get('tools', []))})",
                        file=sys.stderr,
                        flush=True,
                    )
            except (json.JSONDecodeError, KeyError):
                pass

        self._proxy("POST", body)

    def log_message(self, format, *args):
        # Suppress default access logs
        pass


def main():
    global UPSTREAM

    parser = argparse.ArgumentParser(description="Ollama proxy that injects think:false")
    parser.add_argument("--port", type=int, default=PORT, help=f"Proxy port (default {PORT})")
    parser.add_argument("--upstream", default=UPSTREAM, help=f"Upstream Ollama URL (default {UPSTREAM})")
    args = parser.parse_args()

    UPSTREAM = args.upstream

    httpd = http.server.HTTPServer(("127.0.0.1", args.port), OllamaProxy)
    print(f"[ollama-proxy] Listening on 127.0.0.1:{args.port} → {UPSTREAM}", file=sys.stderr, flush=True)
    print(f"[ollama-proxy] Injecting think:false for /api/chat requests with tools", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

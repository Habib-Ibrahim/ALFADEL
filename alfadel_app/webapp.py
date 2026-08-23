from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import json
import threading
import traceback
import webbrowser

from alfadel_core.textio import decode_document_bytes
from .engine import CorrectionEngine

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
RESOURCE_ROOT = ROOT / "resources"
ENGINE = None


def engine():
    global ENGINE
    if ENGINE is None:
        ENGINE = CorrectionEngine(RESOURCE_ROOT)
    return ENGINE


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in {"/", "/index.html"}:
            return self._send(200, (STATIC / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
        if self.path == "/api/status":
            e = engine()
            return self._send(200, json.dumps({
                "name": "ALFADEL",
                "version": "0.1.2",
                "lexicon_entries": len(e.analyzer.lexicon.entries),
                "training_tokens": e.analyzer.training.counts,
                "historical_rules": True,
                "rapidfuzz": e.fuzzy_vocab is not None,
            }, ensure_ascii=False))
        return self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/import-document":
                raw = base64.b64decode(req.get("base64", ""), validate=True)
                name = Path(req.get("filename", "input.txt")).name
                decoded = decode_document_bytes(raw, name)
                return self._send(200, json.dumps({
                    "text": decoded.text,
                    "encoding": decoded.encoding,
                    "kind": decoded.kind,
                    "filename": name,
                }, ensure_ascii=False))
            if self.path == "/api/analyze":
                text = str(req.get("text", ""))
                if len(text) > 2_000_000:
                    return self._send(400, json.dumps({"error": "Text is too large for one pass (maximum 2,000,000 characters)."}))
                mode = str(req.get("editorial_mode", "standardize"))
                if mode not in {"standardize", "preserve"}:
                    mode = "standardize"
                result = engine().analyze(
                    text,
                    historical=True,  # historical-form detection stays active in both editorial modes
                    max_suggestions=max(1, min(8, int(req.get("max_suggestions", 5)))),
                    editorial_mode=mode,
                )
                return self._send(200, json.dumps(result, ensure_ascii=False))
            return self._send(404, json.dumps({"error": "unknown endpoint"}))
        except Exception as exc:
            return self._send(500, json.dumps({"error": str(exc), "trace": traceback.format_exc()}, ensure_ascii=False))


def run_server(port=8766, open_browser=True):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"ALFADEL v0.1.2 running at {url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

"""In-process fake of an OpenAI-compatible chat completions server (loopback only).

A ``responder(request_body) -> reply`` callable decides each reply. A reply is either
``{"content": "text"}`` or ``{"tool_calls": [{"name": ..., "arguments": {...}}]}``. Requests
with ``"stream": true`` get a server-sent-event stream, others a single JSON body.
"""

from __future__ import annotations

import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

MODEL = "fake-model"
_ids = itertools.count(1)


class FakeOpenAI:
    def __init__(self, responder: Callable[[dict], dict]):
        self.responder = responder
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send_json(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path.rstrip("/").endswith("/models"):
                    self._send_json(200, {"object": "list", "data": [
                        {"id": MODEL, "object": "model", "context_length": 131072}]})
                else:
                    self._send_json(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._send_json(404, {"error": {"message": "not found"}})
                    return
                with server._lock:
                    server.requests.append(body)
                reply = server.responder(body)
                if body.get("stream"):
                    self._stream(reply)
                else:
                    self._send_json(200, _completion(reply))

            def _stream(self, reply: dict) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for chunk in _chunks(reply):
                    self.wfile.write(b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def __enter__(self) -> "FakeOpenAI":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _tool_calls(reply: dict) -> list[dict]:
    return [{"id": f"call_{next(_ids)}", "type": "function",
             "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
            for tc in reply.get("tool_calls") or []]


_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def _completion(reply: dict) -> dict:
    calls = _tool_calls(reply)
    message: dict[str, Any] = {"role": "assistant", "content": reply.get("content")}
    if calls:
        message["tool_calls"] = calls
    return {"id": f"chatcmpl-{next(_ids)}", "object": "chat.completion", "created": 0, "model": MODEL,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": _USAGE}


def _chunks(reply: dict) -> list[dict]:
    cid = f"chatcmpl-{next(_ids)}"

    def chunk(delta: dict, finish: str | None = None) -> dict:
        return {"id": cid, "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    out = [chunk({"role": "assistant", "content": ""})]
    calls = _tool_calls(reply)
    if reply.get("content"):
        out.append(chunk({"content": reply["content"]}))
    for i, call in enumerate(calls):
        out.append(chunk({"tool_calls": [{"index": i, "id": call["id"], "type": "function",
                                          "function": {"name": call["function"]["name"], "arguments": ""}}]}))
        out.append(chunk({"tool_calls": [{"index": i, "function": {"arguments": call["function"]["arguments"]}}]}))
    out.append(chunk({}, "tool_calls" if calls else "stop"))
    last = out[-1]
    last["usage"] = _USAGE
    return out

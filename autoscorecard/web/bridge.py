"""Call the FastAPI app directly, without a network: one HTTP request in, one response out.

In the browser a service worker catches the page's /api/... requests (fetch, XHR, the
slide iframes) and hands them to the Python worker, which answers them here. The app
is the same one uvicorn serves locally.
"""
from __future__ import annotations

from urllib.parse import urlsplit


async def handle(app, method: str, url: str, headers: list[tuple[str, str]],
                 body: bytes = b"") -> dict:
    """{"status", "headers": [(name, value)], "body": bytes} for one request."""
    u = urlsplit(url)
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method.upper(), "scheme": u.scheme or "http",
        "path": u.path or "/", "raw_path": (u.path or "/").encode(),
        "query_string": u.query.encode(), "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("browser", 0), "server": (u.hostname or "localhost", u.port or 80),
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    out = {"status": 500, "headers": [], "body": b""}
    chunks: list[bytes] = []

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in msg.get("headers", [])]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await app(scope, receive, send)
    out["body"] = b"".join(chunks)
    return out


def run_threads_inline() -> None:
    """Pyodide has no threads. FastAPI runs plain `def` endpoints, and Starlette reads
    files, in a thread pool via anyio.to_thread.run_sync: run them in place instead."""
    import anyio.to_thread

    async def run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
        return func(*args)

    anyio.to_thread.run_sync = run_sync

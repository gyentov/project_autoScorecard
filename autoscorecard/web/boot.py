"""Start the app inside Pyodide. Called once by web/py-worker.js after the Python files
are unpacked under /app and the browser's own storage is mounted at /data.

    setup(proxy_url, keys, post) -> the FastAPI app

- config: config/config.yaml with the browser's paths, jobs run inline, and LLM calls
  through the Worker at `proxy_url`;
- keys: the key names the Worker holds (e.g. ANTHROPIC_API_KEY). Each gets a marker
  value so the router sees the provider as available. The real keys never reach here;
- post: the synchronous XMLHttpRequest the proxy provider uses.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

APP = Path("/app")
DATA = Path("/data")
KEY_MARKER = "held-by-worker"


def web_config(src: Path, proxy_url: str) -> dict:
    cfg = yaml.safe_load(src.read_text(encoding="utf-8"))
    app = cfg.setdefault("app", {})
    app.update(projects_dir=str(DATA / "projects"), ledger_path=str(DATA / "ledger" / "usage.jsonl"),
               inline_jobs=True, llm_proxy_url=proxy_url)
    return cfg


def setup(proxy_url: str, keys: list[str], post=None):
    from autoscorecard.llm import proxy
    from autoscorecard.web.bridge import run_threads_inline

    out = APP / "config" / "config.web.yaml"
    out.write_text(yaml.safe_dump(web_config(APP / "config" / "config.yaml", proxy_url)),
                   encoding="utf-8")
    os.environ["AUTOSCORECARD_CONFIG"] = str(out)
    for k in keys:
        os.environ[k] = KEY_MARKER
    if post is not None:
        proxy.POST = post
    run_threads_inline()
    (DATA / "projects").mkdir(parents=True, exist_ok=True)
    from autoscorecard.server.app import app
    return app


def js_post(url: str, payload: dict) -> tuple[int, dict[str, str], str]:
    """The proxy transport in the browser: a synchronous XMLHttpRequest (allowed in a
    Web Worker), so the agents' code stays synchronous. Status 0 = network failure."""
    import json

    from js import syncPost   # defined in web/py-worker.js

    r = syncPost(url, json.dumps(payload))
    if r.status == 0:
        raise OSError("network error")
    headers = {}
    for line in str(r.headers).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return int(r.status), headers, str(r.text)


_APP = None


def start(proxy_url: str, keys) -> None:
    global _APP
    _APP = setup(proxy_url, list(keys), js_post)


async def serve(method: str, url: str, headers_json: str, body=None) -> dict:
    """One request from the page (via the service worker) -> the app's response."""
    import json

    from autoscorecard.web.bridge import handle

    if body is not None and hasattr(body, "to_bytes"):
        body = body.to_bytes()
    r = await handle(_APP, method, url, json.loads(headers_json), body or b"")
    return {"status": r["status"], "headers": json.dumps(r["headers"]), "body": r["body"]}

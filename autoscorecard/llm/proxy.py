"""LLM calls through the AutoScorecard Worker (the browser build).

In the browser the API keys must never reach the page, so the providers do not call
Anthropic / OpenAI / Gemini / OpenRouter themselves. They build the same request the SDK
adapters send, as plain JSON, and POST it to the Worker:

    POST {app.llm_proxy_url}/chat   {"provider": "claude_haiku", "agent": ..., "project_id": ...,
                                     "body": <the provider's own request JSON>}

The Worker checks the provider and model against its copy of config.yaml, adds the key,
calls the provider, records the cost in D1 (and refuses over the budget), and returns
the provider's status and JSON unchanged. This module turns that answer into an
LLMResponse, or into the same error categories the router acts on.

`POST` is the transport: urllib here (tests, or a local check against `wrangler dev`);
the browser boot replaces it with a synchronous XMLHttpRequest from the Python worker.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from autoscorecard.config import ProviderConfig
from autoscorecard.llm.base import (
    AuthFailed, BadRequest, OutOfCredit, Provider, RateLimited, Transient,
)
from autoscorecard.llm.formats import (
    ANTHROPIC_STOP, OPENAI_STOP, QUOTA_MARKERS, parse_args, to_anthropic_messages,
    to_openai_messages,
)
from autoscorecard.llm.types import LLMRequest, LLMResponse, ToolCall, Usage

# (url, payload) -> (status, headers with lower-case names, response text)
PostFn = Callable[[str, dict], tuple[int, dict[str, str], str]]


def urllib_post(url: str, payload: dict) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode()


POST: PostFn = urllib_post


def _message(text: str) -> str:
    try:
        err = json.loads(text).get("error")
    except (ValueError, AttributeError):
        return text[:300]
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err or text[:300])


class ProxyProvider(Provider):
    def __init__(self, name: str, cfg: ProviderConfig, url: str, post: PostFn | None = None):
        super().__init__(name, cfg)
        self.url = url.rstrip("/") + "/chat"
        self.post = post

    # ---- the provider's own request JSON
    def body(self, req: LLMRequest) -> dict[str, Any]:
        max_tokens = req.max_tokens or self.cfg.max_tokens
        if self.cfg.type == "anthropic":
            b: dict[str, Any] = {"model": self.cfg.model, "max_tokens": max_tokens,
                                 "system": req.system, "messages": to_anthropic_messages(req.messages),
                                 "cache_control": {"type": "ephemeral"}}
            if req.tools:
                b["tools"] = [{"name": t.name, "description": t.description,
                               "input_schema": t.parameters} for t in req.tools]
            return b
        b = {"model": self.cfg.model, "max_tokens": max_tokens,
             "messages": to_openai_messages(req.system, req.messages)}
        if req.tools:
            b["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in req.tools]
        return b

    def complete(self, req: LLMRequest) -> LLMResponse:
        payload = {"provider": self.name, "agent": req.agent, "project_id": req.project_id,
                   "body": self.body(req)}
        try:
            status, headers, text = (self.post or POST)(self.url, payload)
        except OSError as e:
            raise Transient(self.name, f"proxy unreachable: {e}") from e
        if status != 200:
            self._raise(status, headers, text)
        try:
            data = json.loads(text)
        except ValueError as e:
            raise Transient(self.name, f"proxy returned non-JSON: {text[:200]}") from e
        return self._anthropic(data) if self.cfg.type == "anthropic" else self._openai(data)

    def _raise(self, status: int, headers: dict[str, str], text: str) -> None:
        msg = _message(text)
        low = msg.lower()
        if status in (401, 403):
            raise AuthFailed(self.name, msg)
        if status == 402 or (status == 400 and "credit balance" in low):
            raise OutOfCredit(self.name, msg)
        if status == 429:
            if any(k in low for k in QUOTA_MARKERS):
                raise OutOfCredit(self.name, msg)
            ra = headers.get("retry-after")
            raise RateLimited(self.name, msg, float(ra) if ra else None)
        if status >= 500 or status == 529:
            raise Transient(self.name, msg)
        raise BadRequest(self.name, f"{status}: {msg}")

    # ---- the provider's JSON back into our types
    def _anthropic(self, d: dict) -> LLMResponse:
        content = d.get("content") or []
        u = d.get("usage") or {}
        return LLMResponse(
            text="".join(b.get("text", "") for b in content if b.get("type") == "text"),
            tool_calls=[ToolCall(id=b["id"], name=b["name"], args=dict(b.get("input") or {}))
                        for b in content if b.get("type") == "tool_use"],
            stop=ANTHROPIC_STOP.get(d.get("stop_reason") or "", "other"),
            raw_stop=d.get("stop_reason"),
            usage=Usage(input_tokens=u.get("input_tokens") or 0,
                        output_tokens=u.get("output_tokens") or 0,
                        cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
                        cache_read_tokens=u.get("cache_read_input_tokens") or 0),
            provider=self.name, model=d.get("model") or self.cfg.model)

    def _openai(self, d: dict) -> LLMResponse:
        choice = (d.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = [ToolCall(id=tc["id"], name=tc["function"]["name"],
                          args=parse_args(tc["function"].get("arguments")))
                 for tc in (msg.get("tool_calls") or []) if tc.get("function")]
        u = d.get("usage") or {}
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        stop = OPENAI_STOP.get(choice.get("finish_reason") or "", "other")
        if calls and stop == "end":
            stop = "tool_calls"
        return LLMResponse(
            text=msg.get("content") or "", tool_calls=calls, stop=stop,
            raw_stop=choice.get("finish_reason"),
            usage=Usage(input_tokens=(u.get("prompt_tokens") or 0) - cached,
                        output_tokens=u.get("completion_tokens") or 0, cache_read_tokens=cached),
            provider=self.name, model=self.cfg.model)

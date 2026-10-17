"""Adapter for any OpenAI-compatible Chat Completions endpoint.

Covers OpenAI, Gemini (generativelanguage.googleapis.com/v1beta/openai/),
OpenRouter, and local servers such as Ollama - only base_url and model differ.
"""
from __future__ import annotations

import json
from typing import Any

import openai

from autoscorecard.config import ProviderConfig
from autoscorecard.llm.base import (
    AuthFailed, BadRequest, OutOfCredit, Provider, RateLimited, Transient,
)
from autoscorecard.llm.types import (
    LLMRequest, LLMResponse, Message, Text, ToolCall, ToolResult, Usage,
)

_STOP_MAP = {
    "stop": "end",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "max_tokens",
    "content_filter": "refusal",
}

# Phrases that mean "you're out of money / daily quota", as opposed to a
# short per-minute limit that clears by waiting.
_QUOTA_MARKERS = ("insufficient_quota", "exceeded your current quota", "per day", "perday", "billing")


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        texts = [b.text for b in m.content if isinstance(b, Text)]
        calls = [b for b in m.content if isinstance(b, ToolCall)]
        results = [b for b in m.content if isinstance(b, ToolResult)]
        if m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                msg["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                    for c in calls
                ]
            out.append(msg)
        else:
            # Tool results become separate role="tool" messages, before any user text.
            for r in results:
                content = f"ERROR: {r.content}" if r.is_error else r.content
                out.append({"role": "tool", "tool_call_id": r.call_id, "content": content})
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        return {"_unparsed": raw}


class OpenAICompatProvider(Provider):
    def __init__(self, name: str, cfg: ProviderConfig, client: openai.OpenAI | None = None):
        super().__init__(name, cfg)
        self.client = client or openai.OpenAI(
            api_key=cfg.api_key(), base_url=cfg.base_url, max_retries=0,
        )

    def complete(self, req: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model,
            messages=to_openai_messages(req.system, req.messages),
            max_tokens=req.max_tokens or self.cfg.max_tokens,
        )
        if req.tools:
            kwargs["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in req.tools
            ]
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as e:
            raise AuthFailed(self.name, str(e)) from e
        except openai.PermissionDeniedError as e:
            raise AuthFailed(self.name, str(e)) from e
        except openai.RateLimitError as e:
            msg = str(e)
            if any(k in msg.lower() for k in _QUOTA_MARKERS):
                raise OutOfCredit(self.name, msg) from e
            ra = e.response.headers.get("retry-after") if e.response is not None else None
            raise RateLimited(self.name, msg, float(ra) if ra else None) from e
        except openai.APIStatusError as e:
            if e.status_code == 402:  # OpenRouter: payment required
                raise OutOfCredit(self.name, str(e)) from e
            if e.status_code >= 500:
                raise Transient(self.name, str(e)) from e
            raise BadRequest(self.name, str(e)) from e
        except openai.APIConnectionError as e:
            raise Transient(self.name, str(e)) from e

        choice = resp.choices[0]
        msg = choice.message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, args=_parse_args(tc.function.arguments))
            for tc in (msg.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        u = resp.usage
        cached = 0
        if u is not None and getattr(u, "prompt_tokens_details", None):
            cached = getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0
        usage = Usage(
            input_tokens=(u.prompt_tokens - cached) if u else 0,
            output_tokens=u.completion_tokens if u else 0,
            cache_read_tokens=cached,
        )
        stop = _STOP_MAP.get(choice.finish_reason or "", "other")
        if calls and stop == "end":   # some servers report "stop" even with tool calls
            stop = "tool_calls"
        return LLMResponse(
            text=msg.content or "", tool_calls=calls, stop=stop,
            raw_stop=choice.finish_reason, usage=usage,
            provider=self.name, model=self.cfg.model,
        )

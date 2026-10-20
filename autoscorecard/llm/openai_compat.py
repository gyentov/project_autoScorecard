"""Adapter for any OpenAI-compatible Chat Completions endpoint.

Covers OpenAI, Gemini (generativelanguage.googleapis.com/v1beta/openai/),
OpenRouter, and local servers such as Ollama - only base_url and model differ.
"""
from __future__ import annotations

from typing import Any

import openai

from autoscorecard.config import ProviderConfig
from autoscorecard.llm.base import (
    AuthFailed, BadRequest, OutOfCredit, Provider, RateLimited, Transient,
)
from autoscorecard.llm.formats import (
    OPENAI_STOP as _STOP_MAP, QUOTA_MARKERS as _QUOTA_MARKERS, parse_args as _parse_args,
    to_openai_messages,
)
from autoscorecard.llm.types import LLMRequest, LLMResponse, ToolCall, Usage

__all__ = ["OpenAICompatProvider", "to_openai_messages"]

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

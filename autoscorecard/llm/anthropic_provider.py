"""Claude adapter using the official Anthropic SDK.

Uses top-level automatic prompt caching: the system prompt, tools and earlier
turns are re-read at ~10% of the input price on the next call. Caching only
kicks in above the model's minimum prefix (4096 tokens on Haiku 4.5, 1024 on
Sonnet 5); below that it is a silent no-op, never an error.
"""
from __future__ import annotations

from typing import Any

import anthropic

from autoscorecard.config import ProviderConfig
from autoscorecard.llm.base import (
    AuthFailed, BadRequest, OutOfCredit, Provider, RateLimited, Transient,
)
from autoscorecard.llm.formats import ANTHROPIC_STOP as _STOP_MAP, to_anthropic_messages
from autoscorecard.llm.types import LLMRequest, LLMResponse, ToolCall, Usage

__all__ = ["AnthropicProvider", "to_anthropic_messages"]

class AnthropicProvider(Provider):
    def __init__(self, name: str, cfg: ProviderConfig, client: anthropic.Anthropic | None = None):
        super().__init__(name, cfg)
        # Retries are handled by the router so the policy lives in one place.
        self.client = client or anthropic.Anthropic(api_key=cfg.api_key(), max_retries=0)

    def complete(self, req: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model,
            max_tokens=req.max_tokens or self.cfg.max_tokens,
            system=req.system,
            messages=to_anthropic_messages(req.messages),
            cache_control={"type": "ephemeral"},
        )
        if req.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in req.tools
            ]
        try:
            resp = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise AuthFailed(self.name, e.message) from e
        except anthropic.PermissionDeniedError as e:
            raise AuthFailed(self.name, e.message) from e
        except anthropic.RateLimitError as e:
            ra = e.response.headers.get("retry-after")
            raise RateLimited(self.name, e.message, float(ra) if ra else None) from e
        except anthropic.BadRequestError as e:
            # Anthropic reports an empty balance as a 400 with this wording.
            if "credit balance" in e.message.lower():
                raise OutOfCredit(self.name, e.message) from e
            raise BadRequest(self.name, e.message) from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 or e.status_code == 529:
                raise Transient(self.name, e.message) from e
            raise BadRequest(self.name, e.message) from e
        except anthropic.APIConnectionError as e:
            raise Transient(self.name, str(e)) from e

        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, args=dict(b.input))
            for b in resp.content if b.type == "tool_use"
        ]
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        return LLMResponse(
            text=text, tool_calls=calls,
            stop=_STOP_MAP.get(resp.stop_reason or "", "other"),
            raw_stop=resp.stop_reason, usage=usage,
            provider=self.name, model=self.cfg.model,
        )

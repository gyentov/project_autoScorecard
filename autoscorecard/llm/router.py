"""Picks a provider for each request, enforces budget, retries, and falls back.

Order for a request: the agent's configured provider, then `fallback_chain`.
- Missing key           -> skipped.
- Out of credit / auth  -> disabled for the rest of the session, next provider.
- Rate limited          -> wait (capped) and retry, then next provider.
- Transient (5xx/net)   -> exponential backoff, then next provider.
- Paid provider over the budget -> skipped; free ones (Gemini free, offline) still run.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from autoscorecard.config import ProviderConfig, Settings
from autoscorecard.llm.base import (
    AuthFailed, BadRequest, OutOfCredit, Provider, ProviderError, RateLimited, Transient,
)
from autoscorecard.llm.cost import Ledger, cost_usd
from autoscorecard.llm.ratelimit import RpmLimiter
from autoscorecard.llm.types import LLMRequest, LLMResponse

EventFn = Callable[[dict[str, Any]], None]


class AllProvidersFailed(Exception):
    pass


def build_provider(name: str, cfg: ProviderConfig) -> Provider:
    if cfg.type == "anthropic":
        from autoscorecard.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(name, cfg)
    if cfg.type == "openai_compat":
        from autoscorecard.llm.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(name, cfg)
    from autoscorecard.llm.offline import OfflineProvider
    return OfflineProvider(name, cfg)



class LLMRouter:
    max_attempts = 3
    max_wait_s = 65.0   # longest we'll wait on one rate limit before moving on

    def __init__(self, settings: Settings, ledger: Ledger | None = None,
                 providers: dict[str, Provider] | None = None,
                 on_event: EventFn | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.ledger = ledger or Ledger(settings.app.ledger_path)
        self._providers = providers or {}
        self._disabled: dict[str, str] = {}
        self._limiters: dict[str, RpmLimiter] = {}
        self.on_event = on_event or (lambda e: None)
        self._sleep = sleep

    # ---- helpers -------------------------------------------------------
    def _provider(self, name: str) -> Provider:
        if name not in self._providers:
            p = build_provider(name, self.settings.providers[name])
            if hasattr(p, "language"):
                p.language = self.settings.app.agent_language
            self._providers[name] = p
        return self._providers[name]

    def _limiter(self, name: str) -> RpmLimiter | None:
        rpm = self.settings.providers[name].rpm_limit
        if not rpm:
            return None
        if name not in self._limiters:
            self._limiters[name] = RpmLimiter(rpm, sleep=self._sleep)
        return self._limiters[name]

    def order_for(self, agent: str) -> list[str]:
        first = self.settings.agents.get(agent)
        seq = ([first] if first else []) + self.settings.fallback_chain
        return list(dict.fromkeys(seq))   # dedupe, keep order

    def skip_reason(self, name: str, project_id: str | None) -> str | None:
        cfg = self.settings.providers[name]
        if name in self._disabled:
            return self._disabled[name]
        if name not in self._providers and not cfg.available:
            return f"no API key ({cfg.key_env})"
        if not cfg.price.is_free:
            b = self.settings.budget
            if self.ledger.total_usd >= b.total_usd:
                return f"total budget ${b.total_usd:.2f} reached"
            if project_id and self.ledger.project_usd(project_id) >= b.per_project_usd:
                return f"project budget ${b.per_project_usd:.2f} reached"
        return None

    def status(self) -> list[dict[str, Any]]:
        return [
            {"name": n, "type": c.type, "model": c.model, "free": c.price.is_free,
             "available": self.skip_reason(n, None) is None,
             "reason": self.skip_reason(n, None)}
            for n, c in self.settings.providers.items()
        ]

    # ---- main entry ----------------------------------------------------
    def complete(self, req: LLMRequest) -> LLMResponse:
        errors: list[str] = []
        for name in self.order_for(req.agent):
            reason = self.skip_reason(name, req.project_id)
            if reason:
                errors.append(f"{name}: skipped ({reason})")
                continue
            try:
                resp = self._try_provider(name, req)
            except (OutOfCredit, AuthFailed) as e:
                self._disabled[name] = type(e).__name__
                self.on_event({"type": "provider_disabled", "provider": name, "reason": str(e)})
                errors.append(str(e))
                continue
            except ProviderError as e:
                self.on_event({"type": "provider_failed", "provider": name, "reason": str(e)})
                errors.append(str(e))
                continue

            cfg = self.settings.providers[name]
            resp.cost_usd = cost_usd(resp.usage, cfg.price)
            self.ledger.record(provider=name, model=resp.model, agent=req.agent,
                               project_id=req.project_id, usage=resp.usage, cost=resp.cost_usd)
            self.on_event({"type": "llm_call", "provider": name, "agent": req.agent,
                           "cost_usd": resp.cost_usd, "total_usd": self.ledger.total_usd})
            return resp
        raise AllProvidersFailed("; ".join(errors))

    def _try_provider(self, name: str, req: LLMRequest) -> LLMResponse:
        provider = self._provider(name)
        limiter = self._limiter(name)
        last: ProviderError | None = None
        for attempt in range(self.max_attempts):
            if limiter:
                limiter.acquire(on_wait=lambda s: self.on_event(
                    {"type": "waiting", "provider": name, "seconds": round(s, 1),
                     "why": "rpm_limit", "agent": req.agent}))
            try:
                return provider.complete(req)
            except RateLimited as e:
                last = e
                wait = min(e.retry_after or 2 ** (attempt + 2), self.max_wait_s)
                self.on_event({"type": "waiting", "provider": name, "seconds": wait,
                               "why": "rate_limited", "agent": req.agent})
                self._sleep(wait)
            except Transient as e:
                last = e
                self._sleep(2 ** attempt)
            except BadRequest:
                raise
        assert last is not None
        raise last

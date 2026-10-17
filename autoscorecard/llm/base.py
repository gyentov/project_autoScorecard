"""Provider interface and the error categories the router acts on."""
from __future__ import annotations

from abc import ABC, abstractmethod

from autoscorecard.config import ProviderConfig
from autoscorecard.llm.types import LLMRequest, LLMResponse


class ProviderError(Exception):
    """Base class. `retryable` tells the router whether waiting may help."""
    retryable = False

    def __init__(self, provider: str, message: str):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


class OutOfCredit(ProviderError):
    """Billing exhausted (Claude low balance, OpenAI insufficient_quota, Gemini daily cap)."""


class AuthFailed(ProviderError):
    """Missing or invalid key."""


class RateLimited(ProviderError):
    retryable = True

    def __init__(self, provider: str, message: str, retry_after: float | None = None):
        super().__init__(provider, message)
        self.retry_after = retry_after


class Transient(ProviderError):
    """5xx, overload, network errors."""
    retryable = True


class BadRequest(ProviderError):
    """Our request is wrong; switching provider may still work (different schema rules)."""


class Provider(ABC):
    def __init__(self, name: str, cfg: ProviderConfig):
        self.name = name
        self.cfg = cfg

    @abstractmethod
    def complete(self, req: LLMRequest) -> LLMResponse:
        """Send one request. Raise a ProviderError subclass on failure."""

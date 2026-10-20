"""No-LLM provider: always available, costs nothing.

It never calls tools and answers with a fixed notice, so the deterministic
pipeline keeps running (using default recommendations) when every paid and
free provider is exhausted. Agents detect `provider == "offline"` and fall back
to templated dialogue.
"""
from __future__ import annotations

from autoscorecard.llm.base import Provider
from autoscorecard.llm.types import LLMRequest, LLMResponse, Usage

OFFLINE_NOTICE = {
    "id": "(Mode offline: LLM tidak bisa dipakai saat ini (kuota habis, akses ditolak, atau belum tersambung). Saya lanjut pakai rekomendasi default, Boss.)",
    "en": "(Offline mode: all LLM quota is used up. Continuing with default recommendations.)",
}


class OfflineProvider(Provider):
    language = "id"

    def complete(self, req: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=OFFLINE_NOTICE.get(self.language, OFFLINE_NOTICE["en"]),
            tool_calls=[], stop="end", usage=Usage(),
            provider=self.name, model="offline",
        )

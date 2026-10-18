"""The Boss's input, summarised: one short action sentence for MOMs, follow-up meetings and
the timeline, instead of quoting every word. The exact words stay in the conversation
log (audit).

With an LLM available Laras writes the sentence (one tiny call, ~$0.0005); otherwise a
rule-based summary names the topics the Boss raised.
"""
from __future__ import annotations

import re

from ..llm import LLMRequest, Message
from ..llm.router import AllProvidersFailed

TOPICS = [
    ("timeline", ("timeline", "jadwal", "deadline", "persingkat", "percepat", "lama", "cepat")),
    ("definisi target", ("target", "bad", "dpd", "default", "window", "vintage")),
    ("data dan join", ("data", "join", "tabel", "kolom", "upload")),
    ("pemilihan fitur", ("fitur", "variabel", "feature", "buang", "paksa")),
    ("binning", ("bin", "woe", "potong")),
    ("rencana waterfall", ("waterfall", "stepwise", "korelasi", "iv", "missing", "forward",
                           "backward")),
    ("performa model", ("gini", "ks", "auc", "psi", "performa", "akurasi")),
    ("biaya", ("biaya", "budget", "cost", "mahal")),
    ("presentasi", ("slide", "deck", "presentasi")),
]

PROMPT = ("Kamu Laras, notulis rapat. Ringkas arahan Boss di bawah menjadi SATU kalimat poin "
          "tindakan untuk tim, bahasa Indonesia yang sopan dan profesional, maksimal 20 kata, "
          "tanpa mengutip kata per kata dan tanpa tanda kutip. Tulis hanya kalimatnya.")


def rule_summary(texts: list[str]) -> str:
    words = " ".join(texts).lower()
    found = [name for name, keys in TOPICS if any(re.search(rf"\b{k}", words) for k in keys)]
    if not found:
        return f"Tindak lanjuti {len(texts)} arahan tambahan dari Boss."
    return "Tinjau ulang " + ", ".join(found[:-1]) + (" dan " if len(found) > 1 else "") \
        + found[-1] + " sesuai arahan Boss."


def summarize(office, texts: list[str], literal: bool = False) -> str:
    """One action sentence for what the Boss said ('' when nothing). `literal`: without an
    LLM keep the Boss's own words (an order's label) instead of a topic summary."""
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts:
        return ""
    try:
        resp = office.router.complete(LLMRequest(
            PROMPT, [Message.user("\n".join(f"- {t}" for t in texts))], [], max_tokens=80,
            agent="partner", project_id=office.project.id))
        if resp.provider != "offline":
            out = re.sub(r"<[^>]+>", "", resp.text).strip().strip('"“”')
            if 0 < len(out) < 300:
                return out
    except AllProvidersFailed:
        pass
    if literal:
        t = " ".join(texts)
        return t if len(t) <= 140 else t[:137] + "…"
    return rule_summary(texts)

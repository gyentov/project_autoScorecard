"""Check which LLM providers work, with one tiny request each (costs < $0.001).

    .venv/bin/python scripts/llm_smoke.py            # status only, no calls
    .venv/bin/python scripts/llm_smoke.py --call     # also send a test prompt
    .venv/bin/python scripts/llm_smoke.py --call gemini_free
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoscorecard.config import load_settings          # noqa: E402
from autoscorecard.llm import LLMRequest, LLMRouter, Message  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    settings = load_settings()
    router = LLMRouter(settings, on_event=lambda e: print("  event:", e))

    print("Providers:")
    for s in router.status():
        mark = "OK " if s["available"] else "-- "
        print(f"  {mark}{s['name']:<14} {s['type']:<14} {s['model']:<26} "
              f"{'free' if s['free'] else 'paid'}  {s['reason'] or ''}")
    print(f"Spent so far: ${router.ledger.total_usd:.4f} / ${settings.budget.total_usd:.2f}")

    if "--call" not in args:
        return
    targets = [a for a in args if a != "--call"] or [
        s["name"] for s in router.status() if s["available"] and s["type"] != "offline"]
    for name in targets:
        settings.agents["smoke"] = name
        settings.fallback_chain = [name]
        req = LLMRequest(system="Jawab singkat dalam Bahasa Indonesia.",
                         messages=[Message.user("Sebutkan satu sayuran musim semi.")],
                         max_tokens=50, agent="smoke")
        try:
            r = router.complete(req)
            print(f"[{name}] {r.text.strip()!r}  tokens in/out={r.usage.input_tokens}/"
                  f"{r.usage.output_tokens}  cost=${r.cost_usd:.6f}")
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"[{name}] FAILED: {e}")


if __name__ == "__main__":
    main()

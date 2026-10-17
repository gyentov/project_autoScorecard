"""Laras's honest cost predictions.

An estimate is a RANGE computed from the request that is about to be sent:
    input tokens   ≈ characters / 4 (low) to characters / 3 (high)
    output tokens  ≈ 150 (low) to max_out (high)
    LLM calls      ≈ 1 (low) to max_calls (high); each extra tool round adds the
                     tool results (assumed up to 800 tokens) to the input
    price          from config/config.yaml for the provider that would answer
                   (the agent's first provider that is not skipped)
Cache discounts are ignored, so the high end is conservative.

After the work, the actual cost (from the ledger) is compared with the middle of
the range. Once a project has 3+ such pairs, future ranges are scaled by the
median actual/estimate ratio, and Laras says so.

projects/<id>/work/cost_calibration.json
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass

TOKENS_PER_TOOL_ROUND = 800


@dataclass
class CostEstimate:
    provider: str
    model: str
    free: bool
    low_usd: float
    high_usd: float
    calls: tuple[int, int]
    input_tokens: tuple[int, int]
    output_tokens: tuple[int, int]
    remaining_usd: float
    affordable: bool
    calibration: str = ""

    @property
    def mid_usd(self) -> float:
        return (self.low_usd + self.high_usd) / 2

    def to_dict(self) -> dict:
        return {**asdict(self), "mid_usd": self.mid_usd}

    def say(self) -> str:
        """How Laras says it."""
        if self.provider == "offline":
            return ("Semua kuota LLM sedang tidak tersedia, Boss. Bagian ini jalan tanpa LLM, "
                    "biaya $0.")
        if self.free:
            return f"Gratis, Boss: pakai {self.provider} ({self.model}), tier gratis."
        s = (f"Perkiraan biaya ${self.low_usd:.4f}–${self.high_usd:.4f} "
             f"({self.provider}, {self.calls[0]}–{self.calls[1]} panggilan). "
             f"Sisa budget ${self.remaining_usd:.2f}.")
        if self.calibration:
            s += f" {self.calibration}"
        if not self.affordable:
            s += " Budget tidak cukup untuk batas atas perkiraan ini."
        return s


def _calibration_path(project):
    return project.dir("work") / "cost_calibration.json"


def _ratio(project) -> tuple[float, int]:
    if project is None:
        return 1.0, 0
    p = _calibration_path(project)
    pairs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    ratios = [a["actual"] / a["estimate_mid"] for a in pairs if a["estimate_mid"] > 0]
    if len(ratios) < 3:
        return 1.0, len(ratios)
    return statistics.median(ratios), len(ratios)


def estimate(router, agent: str, prompt_chars: int, project=None, max_out: int = 600,
             max_calls: int = 3) -> CostEstimate:
    s = router.settings
    pid = project.id if project is not None else None
    name = next((n for n in router.order_for(agent) if router.skip_reason(n, pid) is None),
                "offline")
    cfg = s.providers[name]
    pr = cfg.price
    in_lo, in_hi = prompt_chars // 4, prompt_chars // 3 + 1
    out_lo, out_hi = min(150, max_out), max_out
    calls_lo, calls_hi = 1, max_calls
    in_total_hi = sum(in_hi + k * TOKENS_PER_TOOL_ROUND for k in range(calls_hi))
    low = (in_lo * pr.input + out_lo * pr.output) / 1e6
    high = (in_total_hi * pr.input + calls_hi * out_hi * pr.output) / 1e6
    ratio, n = _ratio(project)
    note = ""
    if n >= 3 and abs(ratio - 1) > 0.05:
        low, high = low * ratio, high * ratio
        note = f"(Dikalibrasi dari {n} pekerjaan sebelumnya: biaya aktual ≈ {ratio:.2f}× perkiraan.)"
    spent = router.ledger.total_usd
    remaining = max(s.budget.total_usd - spent, 0.0)
    if pid:
        remaining = min(remaining, max(s.budget.per_project_usd - router.ledger.project_usd(pid), 0.0))
    return CostEstimate(name, cfg.model or name, pr.is_free, round(low, 6), round(high, 6),
                        (calls_lo, calls_hi), (in_lo, in_total_hi), (out_lo, calls_hi * out_hi),
                        round(remaining, 4), pr.is_free or high <= remaining, note)


def record_actual(project, est: CostEstimate, actual_usd: float, what: str) -> dict:
    """Store actual vs estimate (paid providers only) and return the comparison."""
    rec = {"what": what, "provider": est.provider, "estimate_low": est.low_usd,
           "estimate_high": est.high_usd, "estimate_mid": est.mid_usd,
           "actual": round(actual_usd, 6),
           "within_range": est.low_usd - 1e-9 <= actual_usd <= est.high_usd + 1e-9}
    if project is not None and not est.free and est.mid_usd > 0:
        p = _calibration_path(project)
        pairs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        pairs.append(rec)
        p.write_text(json.dumps(pairs, indent=2), encoding="utf-8")
    return rec


def say_actual(rec: dict) -> str:
    if rec["actual"] == 0:
        return "Biaya aktual: $0."
    verdict = "sesuai perkiraan" if rec["within_range"] else (
        "di atas perkiraan, maaf Boss" if rec["actual"] > rec["estimate_high"]
        else "di bawah perkiraan")
    return f"Biaya aktual ${rec['actual']:.4f} ({verdict})."

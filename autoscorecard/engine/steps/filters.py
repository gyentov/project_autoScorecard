"""Built-in filter steps: missing rate, identical rate, Information Value."""
from __future__ import annotations

from .base import StepContext, StepResult, register_step


@register_step(
    "missing_rate", title="Missing rate", defaults={"max": 0.30}, unit_params=("max",),
    help="Share of rows where the feature is empty. Measured on the training sample.",
    criterion=lambda p: f"missing rate < {p['max']:.0%}")
def missing_rate(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    st = ctx.stats
    return StepResult([{"feature": f, "reason": f"missing rate {st.loc[f, 'missing_rate']:.1%} "
                                                f">= {p['max']:.0%}"}
                       for f in pool if st.loc[f, "missing_rate"] >= p["max"]])


@register_step(
    "identical_rate", title="Identical rate", defaults={"max": 0.95, "include_missing": True},
    unit_params=("max",),
    help="Share of rows holding the single most frequent value (missing counts as a value "
         "unless include_missing is false). Catches near-constant features.",
    criterion=lambda p: f"identical rate < {p['max']:.0%}")
def identical_rate(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    st = ctx.stats
    col = "identical_rate" if p["include_missing"] else "identical_rate_non_missing"
    return StepResult([{"feature": f, "reason": f"identical rate {st.loc[f, col]:.1%} >= "
                                                f"{p['max']:.0%} (value {st.loc[f, 'top_value']!r})"}
                       for f in pool if st.loc[f, col] >= p["max"]])


def _check_iv(p: dict) -> None:
    if p["min"] >= p["max"]:
        raise ValueError("iv.min must be below iv.max")


@register_step(
    "iv", title="Information Value", defaults={"min": 0.02, "max": 0.5},
    unit_params=("min", "max"), validate=_check_iv,
    help="IV of the monotonic WoE binning. Below min = no predictive power; above max = "
         "suspiciously strong, often leakage from the outcome.",
    criterion=lambda p: f"{p['min']:g} < IV < {p['max']:g}")
def information_value(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    ok, dropped = ctx.binned(pool)
    for f in ok:
        iv = ctx.iv(f)
        if iv <= p["min"]:
            dropped.append({"feature": f, "reason": f"IV {iv:.4f} <= {p['min']:g}"})
        elif iv >= p["max"]:
            dropped.append({"feature": f, "reason": f"IV {iv:.4f} >= {p['max']:g} "
                                                    "(check for leakage)"})
    return StepResult(dropped)

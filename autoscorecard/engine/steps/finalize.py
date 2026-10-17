"""Built-in step: finalization, including forced features."""
from __future__ import annotations

from ..logistic import fit_logit
from ..selection import vif
from .base import StepContext, StepResult, register_step

FAIL = "forced into the model but still fails: "


def _finalize(W, y, pool, p, forced: set[str] = frozenset()) -> tuple[list[str], list[dict], dict]:
    """Fix one problem at a time. Forced features face the same sign / p-value /
    VIF checks (that is the test they were forced in for) but do not count
    towards max_features."""
    sel = list(pool)
    dropped = []
    while sel:
        m = fit_logit(W[sel], y)
        pos = [f for f in sel if m.coef[f] > 0] if p["require_negative"] else []
        high_p = [f for f in sel if m.p_value[f] >= p["max_p_value"]]
        v = vif(W[sel])
        high_v = [f for f in sel if v[f] >= p["max_vif"]]
        if pos:
            f = max(pos, key=lambda g: m.coef[g])
            why = f"positive coefficient {m.coef[f]:.4f}"
        elif high_p:
            f = max(high_p, key=lambda g: m.p_value[g])
            why = f"p-value {m.p_value[f]:.4g} >= {p['max_p_value']}"
        elif high_v:
            f = max(high_v, key=lambda g: v[g])
            why = f"VIF {v[f]:.2f} >= {p['max_vif']}"
        elif len(set(sel) - forced) > p["max_features"]:
            f = min(set(sel) - forced, key=lambda g: abs(m.z[g]))
            why = f"more than {p['max_features']} features; weakest |z|={abs(m.z[f]):.2f}"
        else:
            return sel, dropped, {"model": m.to_dict(), "vif": v.to_dict()}
        sel.remove(f)
        dropped.append({"feature": f, "reason": (FAIL + why) if f in forced else why})
    raise ValueError("finalization removed every feature")


def _check(p: dict) -> None:
    if int(p["max_features"]) < 1:
        raise ValueError("finalization.max_features must be at least 1")
    if float(p["max_vif"]) <= 1:
        raise ValueError("finalization.max_vif must be above 1")


@register_step(
    "finalization", title="Finalization", kind="model", must_be_last=True, accepts_forced=True,
    defaults={"require_negative": True, "max_p_value": 0.05, "max_vif": 5.0, "max_features": 15},
    unit_params=("max_p_value",), validate=_check,
    help="Final checks on the model, fixed one feature at a time: positive coefficient, then "
         "p-value, then VIF, then the feature count (weakest |z| out first). Forced features "
         "are added back here and discarded only if they still fail.",
    criterion=lambda p: (f"p < {p['max_p_value']:g}, VIF < {p['max_vif']:g}, "
                         f"max {p['max_features']} features"
                         + (" and all coefficients < 0" if p.get("require_negative") else "")))
def finalization(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    forced = set(ctx.forced)
    ok, dropped = ctx.binned(pool)
    if not ok and not forced:
        raise ValueError("Finalization: no features left to work with")
    kept, more, detail = _finalize(ctx.woe(ok), ctx.y, ok, p, forced) if ok else ([], [], {})
    dropped += more
    if not ctx.forced:
        return StepResult(dropped, detail)

    # bring back forced features the earlier steps removed, then finalize again
    failed_here = {d["feature"] for d in dropped}
    back = [f for f in ctx.forced if f not in kept and f not in failed_here]
    back_ok, back_bad = ctx.binned(back)
    kept, more, detail = _finalize(ctx.woe(kept + back_ok), ctx.y, kept + back_ok, p, forced)
    dropped += [{"feature": d["feature"], "reason": FAIL + d["reason"]} for d in back_bad] + more
    reason_now = {d["feature"]: d["reason"] for d in dropped}
    detail["forced"] = [{
        "feature": f,
        "previously_dropped_at": ctx.earlier_drop(f)[0],
        "previous_reason": ctx.earlier_drop(f)[1],
        "result": "kept" if f in kept else "discarded",
        "reason": "" if f in kept else reason_now.get(f, "")} for f in ctx.forced]
    return StepResult(dropped, detail, kept=kept, added=len(back))

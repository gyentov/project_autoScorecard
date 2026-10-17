"""Built-in steps: stepwise, forward and backward selection on WoE features."""
from __future__ import annotations

from ..logistic import chi2_sf_df1, fit_logit
from ..selection import backward, forward, stepwise
from .base import StepContext, StepResult, register_step


def _neg(p: dict) -> str:
    return " and all coefficients < 0" if p.get("require_negative") else ""


def _check_max(p: dict) -> None:
    if int(p["max_features"]) < 1:
        raise ValueError("max_features must be at least 1")


def _not_entered(W, y, selected, pool, p_enter, require_negative) -> list[dict]:
    """Reason for every candidate a forward-type step left out, measured against
    the final selection (one extra fit per candidate)."""
    base = fit_logit(W[selected], y)
    out = []
    for f in pool:
        if f in selected:
            continue
        m = fit_logit(W[selected + [f]], y)
        if require_negative and any(m.coef[g] > 0 for g in m.features):
            pos = [g for g in m.features if m.coef[g] > 0]
            out.append({"feature": f, "reason": f"adding it makes coefficient(s) positive: {pos}"})
        else:
            pv = chi2_sf_df1(2 * (m.log_likelihood - base.log_likelihood))
            out.append({"feature": f, "reason": f"not entered: LR p={pv:.4g} >= {p_enter} "
                                                f"given the selected features"})
    return out


def _removed_or_not_entered(res, W, y, pool, p_enter, require_negative) -> list[dict]:
    removed = {s["feature"]: s["reason"] for s in res.steps if s["action"] == "remove"
               and s["feature"] not in res.selected}
    rest = [f for f in pool if f not in removed]
    return ([{"feature": f, "reason": f"removed: {r}"} for f, r in removed.items()]
            + _not_entered(W, y, res.selected, rest, p_enter, require_negative))


def _prepare(pool, ctx, title):
    ok, dropped = ctx.binned(pool)
    if not ok:
        raise ValueError(f"{title}: no features left to work with")
    return ok, dropped, ctx.woe(ok)


@register_step(
    "stepwise", title="Stepwise selection", kind="model",
    defaults={"p_enter": 0.01, "p_remove": 0.05, "max_features": 15, "require_negative": True},
    unit_params=("p_enter", "p_remove"), validate=_check_max,
    help="Add the feature with the smallest likelihood-ratio p-value (< p_enter), then remove "
         "any whose Wald p-value rose above p_remove. Repeat.",
    criterion=lambda p: (f"enter p < {p['p_enter']:g}, stay p < {p['p_remove']:g}, "
                         f"max {p['max_features']} features{_neg(p)}"))
def stepwise_step(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    ok, dropped, W = _prepare(pool, ctx, "Stepwise selection")
    res = stepwise(W, ctx.y, p["p_enter"], p["p_remove"], p["max_features"],
                   p["require_negative"], ok)
    dropped += _removed_or_not_entered(res, W, ctx.y, ok, p["p_enter"], p["require_negative"])
    return StepResult(dropped, {"steps": res.steps}, trace=res.steps_table())


@register_step(
    "forward", title="Forward selection", kind="model",
    defaults={"p_enter": 0.01, "max_features": 15, "require_negative": True},
    unit_params=("p_enter",), validate=_check_max,
    help="Start empty and add features one by one while the best LR p-value < p_enter.",
    criterion=lambda p: f"enter p < {p['p_enter']:g}, max {p['max_features']} features{_neg(p)}")
def forward_step(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    ok, dropped, W = _prepare(pool, ctx, "Forward selection")
    res = forward(W, ctx.y, p["p_enter"], p["max_features"], p["require_negative"], ok)
    dropped += _removed_or_not_entered(res, W, ctx.y, ok, p["p_enter"], p["require_negative"])
    return StepResult(dropped, {"steps": res.steps}, trace=res.steps_table())


@register_step(
    "backward", title="Backward elimination", kind="model",
    defaults={"p_remove": 0.05, "require_negative": True}, unit_params=("p_remove",),
    help="Start with all features and remove the one with the largest Wald p-value while it "
         "is above p_remove.",
    criterion=lambda p: f"stay p < {p['p_remove']:g}{_neg(p)}")
def backward_step(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    ok, dropped, W = _prepare(pool, ctx, "Backward elimination")
    res = backward(W, ctx.y, p["p_remove"], p["require_negative"], ok)
    dropped += [{"feature": s["feature"], "reason": s["reason"]} for s in res.steps]
    return StepResult(dropped, {"steps": res.steps}, trace=res.steps_table())

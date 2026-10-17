"""Built-in step: multicollinearity clustering on the WoE correlation matrix."""
from __future__ import annotations

from ..selection import correlation_clusters
from .base import StepContext, StepResult, register_step


def _check(p: dict) -> None:
    if p["linkage"] not in ("single", "complete", "average"):
        raise ValueError("correlation_cluster.linkage must be single, complete or average")


@register_step(
    "correlation_cluster", title="Multicollinearity clustering",
    defaults={"threshold": 0.7, "linkage": "average"}, unit_params=("threshold",),
    validate=_check,
    help="Hierarchical clustering of the WoE correlation matrix, distance 1-|corr|. Features "
         "linked at |corr| >= threshold form a cluster; only the highest-IV member stays.",
    criterion=lambda p: (f"|corr| < {p['threshold']:g} between kept features "
                         f"({p['linkage']} linkage, highest IV kept)"))
def correlation_cluster(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    ok, dropped = ctx.binned(pool)
    if not ok:
        raise ValueError("Multicollinearity clustering: no features left to work with")
    cl = correlation_clusters(ctx.woe(ok), {f: ctx.iv(f) for f in ok}, p["threshold"],
                              p["linkage"])
    return StepResult(dropped + cl.dropped, {"clusters": cl.clusters}, clusters=cl)

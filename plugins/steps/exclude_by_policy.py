"""Example custom waterfall step: drop features that policy does not allow.

Some variables may not be used for credit decisions whatever their IV (for
example gender, religion or ethnicity, depending on regulation and internal
policy). This step drops every feature whose name matches one of `patterns`
(shell-style wildcards, case-insensitive) and records why.

Use it in config/config.yaml (or a project's plan), typically first:

    - {type: exclude_by_policy, params: {patterns: ["*gender*", "*religion*"],
                                         reason: "not allowed by credit policy"}}

This file is also the template for writing your own step: copy it, change the
name and the function, and it appears in the plan editor and the report.
"""
from __future__ import annotations

import fnmatch

from autoscorecard.engine.steps import StepContext, StepResult, register_step


def _check(p: dict) -> None:
    if not isinstance(p["patterns"], list) or not all(isinstance(x, str) for x in p["patterns"]):
        raise ValueError("exclude_by_policy.patterns must be a list of name patterns")


@register_step(
    "exclude_by_policy", title="Excluded by policy",
    defaults={"patterns": [], "reason": "not allowed by policy"}, validate=_check,
    help="Drops features whose name matches a policy pattern (e.g. *gender*), whatever "
         "their predictive power.",
    criterion=lambda p: ("name does not match " + ", ".join(p["patterns"])
                         if p["patterns"] else "no patterns set (keeps everything)"))
def exclude_by_policy(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    pats = [x.lower() for x in p["patterns"]]
    dropped = []
    for f in pool:
        hit = next((pat for pat in pats if fnmatch.fnmatch(f.lower(), pat)), None)
        if hit:
            dropped.append({"feature": f, "reason": f"{p['reason']} (matches {hit!r})"})
    return StepResult(dropped)

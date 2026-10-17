# Extending the waterfall: custom steps

Every waterfall step, built-in or custom, is **one Python function registered
with `@register_step`**. Once registered, a step can be used in plans, and gets
validation, the plan diagram, the report, the audit log and the plan-editor API
(`GET /api/waterfall/step-types`) without any other code change.

Built-in steps: `autoscorecard/engine/steps/` (`filters.py`, `correlation.py`,
`model_selection.py`, `finalize.py`).
Custom steps: any `*.py` file in **`plugins/steps/`**, loaded when the config
loads. The folder list is `modelling.step_plugins` in `config/config.yaml`.
Files starting with `_` are skipped.

## Writing a step

Copy `plugins/steps/exclude_by_policy.py` (a working example) and change it:

```python
from autoscorecard.engine.steps import StepContext, StepResult, register_step


@register_step(
    "max_missing_in_recent",                 # the name used in plans
    title="Recent missing rate",             # shown in the plan, diagram and report
    defaults={"max": 0.2},                   # every parameter, with its default
    unit_params=("max",),                    # parameters that must lie in [0, 1]
    help="Missing rate in the last 20% of training rows (file order; sort by date first).",
    criterion=lambda p: f"recent missing rate < {p['max']:.0%}",
)
def max_missing_in_recent(pool: list[str], p: dict, ctx: StepContext) -> StepResult:
    recent = ctx.X.tail(len(ctx.X) // 5)
    dropped = [{"feature": f, "reason": f"recent missing rate {recent[f].isna().mean():.1%}"}
               for f in pool if recent[f].isna().mean() >= p["max"]]
    return StepResult(dropped)
```

Then put it in a plan like any built-in step:

```yaml
- {type: max_missing_in_recent, params: {max: 0.2}}
```

## What a step receives

| argument | content |
|---|---|
| `pool` | the features still in the waterfall (a copy; changing it has no effect) |
| `p` | the step's parameters: your `defaults` merged with the plan's values |
| `ctx.X`, `ctx.y` | training sample (raw values after outlier treatment) and target (1 = bad) |
| `ctx.stats` | per feature: `missing_rate`, `identical_rate`, `identical_rate_non_missing`, `top_value` |
| `ctx.binned(pool)` | `(binnable features, drops for the rest)`: bins on first use, monotonic, with manual edits applied |
| `ctx.woe(features)` | DataFrame of WoE values (same binning the model uses) |
| `ctx.iv(f)` | IV of a binned feature |
| `ctx.forced` | forced features (see finalization) |
| `ctx.history`, `ctx.earlier_drop(f)` | outcomes of earlier steps; which step dropped `f` and why |

Use `ctx.binned` and `ctx.woe` rather than binning yourself, so every step sees
the same WoE as the final model.

## What a step returns

`StepResult(dropped, detail={}, kept=None, added=0, trace=None, clusters=None)`

- `dropped`: `[{"feature": ..., "reason": ...}]`. **Every drop needs a reason**:
  it goes to the report, `waterfall_features.csv` and the audit log.
- `detail`: anything worth showing (stored with the step outcome).
- `kept` / `added`: only for a step that rebuilds the pool itself, as finalization
  does when it re-adds forced features.
- `trace`: a selection trace table (appended to `selection_steps.csv`).

The runner checks the bookkeeping of every step: in + added − dropped = out, and
no feature dropped twice. A step that miscounts stops the build with an error
rather than silently losing a feature.

## Registration options

| option | meaning |
|---|---|
| `validate=fn` | `fn(params)` raises `ValueError` for bad values; shown to the user when the plan is saved |
| `unit_params=(...)` | parameters checked to lie in [0, 1] |
| `kind="model"` | the step uses the regression (plan warnings treat filters placed after it as unusual) |
| `must_be_last=True` | must be the last enabled step |
| `accepts_forced=True` | the step brings forced features back in (finalization) |

A plugin cannot reuse the name of an existing step: the second registration is
refused. A plugin that fails to import stops config loading with the file name
in the error.

## Tests

Plugin steps are ordinary functions: test them by building a small DataFrame
and calling `run_waterfall` with a plan containing the step. See
`tests/test_steps.py`.

import numpy as np
import pandas as pd
import pytest

from autoscorecard.config import ROOT
from autoscorecard.engine.steps import (BUILTIN_ORDER, REGISTRY, StepResult, load_plugins,
                                        register_step)
from autoscorecard.engine.waterfall import parse_steps, plan_warnings, run_waterfall
from autoscorecard.engine.waterfall_report import plan_markdown, report_markdown


@pytest.fixture
def data():
    rng = np.random.default_rng(3)
    n = 3000
    a, b = rng.normal(size=n), rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(2.2 - 0.6 * a - 0.4 * b))).astype(int)
    X = pd.DataFrame({"a": a, "b": b, "gender": rng.choice(["F", "M"], n),
                      "religion_code": rng.integers(1, 6, n)})
    return X, pd.Series(y)


@pytest.fixture
def temp_steps():
    """Register test steps and remove them again afterwards."""
    names = []
    yield names
    for n in names:
        REGISTRY.pop(n, None)


def test_builtins_registered_in_order():
    assert list(REGISTRY)[:8] == BUILTIN_ORDER
    assert all(REGISTRY[t].source == "builtin" for t in BUILTIN_ORDER)
    assert REGISTRY["finalization"].must_be_last and REGISTRY["finalization"].accepts_forced
    assert {REGISTRY[t].kind for t in ("stepwise", "forward", "backward")} == {"model"}


def test_custom_step_runs_and_is_reported(data, temp_steps):
    X, y = data

    @register_step("drop_b_for_test", title="Drop b (test)", defaults={"why": "test"},
                   help="Drops b.", criterion=lambda p: "feature is not b")
    def _drop_b(pool, p, ctx):
        return StepResult([{"feature": "b", "reason": p["why"]}] if "b" in pool else [])
    temp_steps.append("drop_b_for_test")

    steps = parse_steps([{"type": "drop_b_for_test", "params": {"why": "because"}},
                         {"type": "iv", "params": {"min": 0.0}}, {"type": "finalization"}])
    res = run_waterfall(X[["a", "b"]], y, ["a", "b"], steps)
    assert "b" not in res.selected
    assert res.dropped().set_index("feature").loc["b", "step"] == "Drop b (test)"
    assert "Drop b (test)" in plan_markdown(steps) and "feature is not b" in report_markdown(res)


def test_runner_rejects_steps_that_miscount(data, temp_steps):
    X, y = data

    @register_step("bad_step_for_test", title="Bad", defaults={}, help="", criterion=lambda p: "")
    def _bad(pool, p, ctx):
        return StepResult([{"feature": "a", "reason": "x"}, {"feature": "a", "reason": "y"}])
    temp_steps.append("bad_step_for_test")
    with pytest.raises(RuntimeError, match="do not add up"):
        run_waterfall(X, y, ["a", "b"], parse_steps([{"type": "bad_step_for_test"}]))


def test_validation_hooks(temp_steps):
    def check(p):
        if p["n"] < 0:
            raise ValueError("n must be positive")

    @register_step("val_for_test", title="V", defaults={"n": 1, "rate": 0.5}, help="",
                   criterion=lambda p: "", validate=check, unit_params=("rate",),
                   must_be_last=True)
    def _v(pool, p, ctx):
        return StepResult()
    temp_steps.append("val_for_test")
    with pytest.raises(ValueError, match="positive"):
        parse_steps([{"type": "val_for_test", "params": {"n": -1}}])
    with pytest.raises(ValueError, match="between 0 and 1"):
        parse_steps([{"type": "val_for_test", "params": {"rate": 2}}])
    with pytest.raises(ValueError, match="last"):
        parse_steps([{"type": "val_for_test"}, {"type": "iv"}])


def test_name_clash_is_refused():
    with pytest.raises(ValueError, match="already registered"):
        register_step("iv", title="x", defaults={}, help="", criterion=lambda p: "")(
            lambda pool, p, ctx: StepResult())


def test_plugin_folder_loading(tmp_path, temp_steps):
    (tmp_path / "my_step.py").write_text(
        "from autoscorecard.engine.steps import StepResult, register_step\n"
        "@register_step('from_plugin_for_test', title='P', defaults={}, help='h',"
        " criterion=lambda p: 'c')\n"
        "def run(pool, p, ctx):\n    return StepResult()\n")
    (tmp_path / "_ignored.py").write_text("raise SystemExit('must not be imported')\n")
    temp_steps.append("from_plugin_for_test")
    assert load_plugins([tmp_path]) == ["from_plugin_for_test"]
    assert REGISTRY["from_plugin_for_test"].source == "autoscorecard_plugins.my_step"
    assert load_plugins([tmp_path]) == []                         # loaded once only
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "oops.py").write_text("this is not python\n")
    with pytest.raises(ValueError, match="oops.py failed to load"):
        load_plugins([broken])


def test_example_policy_plugin(data):
    load_plugins(["plugins/steps"], root=ROOT)
    X, y = data
    steps = parse_steps([{"type": "exclude_by_policy",
                          "params": {"patterns": ["*gender*", "religion*"]}},
                         {"type": "iv", "params": {"min": 0.0}}, {"type": "finalization"}])
    res = run_waterfall(X, y, list(X.columns), steps)
    d = res.dropped().set_index("feature")
    assert d.loc["gender", "step"] == "Excluded by policy"
    assert "matches '*gender*'" in d.loc["gender", "reason"]
    assert "religion_code" in d.index and set(res.selected) <= {"a", "b"}
    assert steps[0].criterion == "name does not match *gender*, religion*"
    assert "after model selection" not in " ".join(plan_warnings(steps))
    with pytest.raises(ValueError, match="list of name patterns"):
        parse_steps([{"type": "exclude_by_policy", "params": {"patterns": "gender"}}])

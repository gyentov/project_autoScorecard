import xml.dom.minidom

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from autoscorecard.engine.pipeline import ScorecardConfig, build_scorecard
from autoscorecard.engine.waterfall import (DEFAULT_WATERFALL, STEP_TYPES, approve_plan,
                                            load_plan, parse_steps, plan_warnings, run_waterfall,
                                            save_plan)
from autoscorecard.engine.waterfall_report import (plan_markdown, plan_svg, report_markdown,
                                                   waterfall_svg)
from autoscorecard.store import ProjectStore


@pytest.fixture(scope="module")
def data():
    """Two real drivers, a correlated copy, a noise column, a leak, a mostly-missing
    column and a near-constant column - one for each waterfall step to catch."""
    rng = np.random.default_rng(11)
    n = 6000
    a, b = rng.normal(size=n), rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(2.2 - 0.6 * a - 0.4 * b))).astype(int)
    X = pd.DataFrame({
        "a": a, "b": b,
        "a_copy": a + rng.normal(scale=0.2, size=n),
        "noise": rng.normal(size=n),
        "leak": y + rng.normal(scale=0.05, size=n),
        "mostly_missing": np.where(rng.random(n) < 0.4, np.nan, a),
        "constant_ish": np.where(rng.random(n) < 0.97, 0.0, rng.normal(size=n)),
    })
    return X, pd.Series(y)


def test_default_plan_order_and_criteria():
    steps = parse_steps()
    assert [s.type for s in steps] == ["missing_rate", "identical_rate", "iv",
                                       "correlation_cluster", "stepwise", "forward",
                                       "backward", "finalization"]
    crit = [s.criterion for s in steps]
    assert crit[0] == "missing rate < 30%"
    assert crit[1] == "identical rate < 95%"
    assert crit[2] == "0.02 < IV < 0.5"
    assert crit[3].startswith("|corr| < 0.7")
    assert plan_warnings(steps) == []


def test_config_plan_matches_engine_default():
    from autoscorecard.config import load_settings
    cfg = [s.to_dict() for s in parse_steps(load_settings().modelling.waterfall)]
    assert cfg == [s.to_dict() for s in parse_steps(DEFAULT_WATERFALL)]


@pytest.mark.parametrize("spec, msg", [
    ([{"type": "magic"}], "unknown type"),
    ([{"type": "iv", "params": {"minimum": 0.1}}], "unknown parameter"),
    ([{"type": "iv"}, {"type": "iv"}], "twice"),
    ([{"type": "finalization"}, {"type": "iv"}], "last"),
    ([{"type": "iv", "params": {"min": 0.6}}], "below"),
    ([{"type": "missing_rate", "params": {"max": 30}}], "between 0 and 1"),
])
def test_bad_plans_are_rejected(spec, msg):
    with pytest.raises(ValueError, match=msg):
        parse_steps(spec)


def test_reordered_plan_warns():
    steps = parse_steps([{"type": "stepwise"}, {"type": "iv"}])
    w = " ".join(plan_warnings(steps))
    assert "after model selection" in w and "correlation" in w


def test_each_step_drops_what_it_should(data):
    X, y = data
    res = run_waterfall(X, y, list(X.columns), parse_steps())
    at = res.dropped().set_index("feature")["step"].to_dict()
    assert at["mostly_missing"] == "Missing rate"
    assert at["constant_ish"] == "Identical rate"
    assert at["leak"] == "Information Value"                  # IV >= 0.5
    assert at["noise"] == "Information Value"                 # IV <= 0.02
    assert at["a_copy"] == "Multicollinearity clustering"
    assert res.selected == ["a", "b"]


def test_waterfall_bookkeeping(data):
    X, y = data
    res = run_waterfall(X, y, list(X.columns), parse_steps())
    s = res.summary()
    assert s.loc[0, "n_out"] == len(X.columns)
    assert (s["n_in"] - s["n_dropped"] == s["n_out"]).all()
    assert (s["n_out"].iloc[:-1].to_numpy() == s["n_in"].iloc[1:].to_numpy()).all()
    d = res.dropped()
    assert d["feature"].is_unique                             # dropped exactly once
    assert set(d["feature"]) | set(res.selected) == set(X.columns)
    assert (d["reason"].str.len() > 0).all()
    j = res.feature_journey()
    assert set(j.loc[j.status == "selected", "feature"]) == set(res.selected)


def test_disabled_and_reordered_steps(data):
    X, y = data
    plan = [{"type": "missing_rate", "enabled": False}, {"type": "iv"},
            {"type": "identical_rate"}, {"type": "finalization"}]
    res = run_waterfall(X, y, list(X.columns), parse_steps(plan))
    assert [o.step.type for o in res.outcomes] == ["iv", "identical_rate", "finalization"]
    # missing_rate skipped, so mostly_missing reaches the IV step and is judged there
    assert "mostly_missing" not in set(res.dropped().query("step == 'Missing rate'").feature)


def test_finalization_enforces_limits(data):
    X, y = data
    plan = [{"type": "finalization", "params": {"max_features": 1, "max_vif": 5.0}}]
    res = run_waterfall(X[["a", "b", "noise"]], y, ["a", "b", "noise"], parse_steps(plan))
    assert res.selected == ["a"]
    assert all(res.model.coef[f] < 0 for f in res.selected)


def test_reports_render(data):
    X, y = data
    res = run_waterfall(X, y, list(X.columns), parse_steps())
    md = report_markdown(res)
    for t in STEP_TYPES.values():
        if t.source == "builtin":
            assert t.title in md
    assert "```mermaid" in md and "Every feature" in md
    for svg in (waterfall_svg(res.summary()), plan_svg(res.steps)):
        xml.dom.minidom.parseString(svg)                      # well-formed
    assert "waiting for approval" in plan_markdown(res.steps)


def test_build_needs_approval_when_required(data, tmp_path):
    X, y = data
    proj = ProjectStore(tmp_path).create("wf")
    save_plan(proj, parse_steps([{"type": "iv"}, {"type": "correlation_cluster"},
                                 {"type": "stepwise"}]))
    with pytest.raises(PermissionError):
        build_scorecard(X, y, list(X.columns), ScorecardConfig(), project=proj,
                        require_approved=True)
    approve_plan(proj)
    res = build_scorecard(X, y, list(X.columns), ScorecardConfig(), project=proj,
                          require_approved=True)
    assert [o.step.type for o in res.waterfall.outcomes] == ["iv", "correlation_cluster",
                                                             "stepwise"]   # project plan used
    # editing the plan clears the approval
    save_plan(proj, parse_steps([{"type": "iv"}]))
    assert load_plan(proj)["approved"] is False


def test_waterfall_api(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    monkeypatch.setattr(appmod, "store", ProjectStore(tmp_path))
    c = TestClient(appmod.app)
    assert {t["type"] for t in c.get("/api/waterfall/step-types").json()} == set(STEP_TYPES)
    pid = c.post("/api/projects", json={"name": "W"}).json()["id"]
    plan = c.get(f"/api/projects/{pid}/waterfall").json()
    assert len(plan["steps"]) == 8 and plan["approved"] is False and "flowchart" in plan["mermaid"]
    new = list(reversed(plan["steps"]))
    assert c.put(f"/api/projects/{pid}/waterfall", json={"steps": new}).status_code == 400
    new = plan["steps"][:3] + [{"type": "finalization"}]
    r = c.put(f"/api/projects/{pid}/waterfall", json={"steps": new}).json()
    assert [s["type"] for s in r["steps"]] == ["missing_rate", "identical_rate", "iv",
                                              "finalization"]
    assert c.post(f"/api/projects/{pid}/waterfall/approve").json()["approved"] is True
    assert c.get(f"/api/projects/{pid}/waterfall/plan.svg").text.startswith("<svg")

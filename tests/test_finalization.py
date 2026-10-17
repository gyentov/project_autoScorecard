import json
import xml.dom.minidom

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from autoscorecard.engine import finalization as fin
from autoscorecard.engine.binning import bin_numeric
from autoscorecard.engine.pipeline import ScorecardConfig, build_scorecard
from autoscorecard.engine.waterfall import parse_steps, run_waterfall
from autoscorecard.engine.waterfall_report import correlation_heatmap_svg, report_markdown
from autoscorecard.store import ProjectStore

PLAN = parse_steps()


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(21)
    n = 6000
    a, b, c = rng.normal(size=n), rng.normal(size=n), rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(2.2 - 0.6 * a - 0.4 * b - 0.12 * c))).astype(int)
    X = pd.DataFrame({"a": a, "b": b, "c": c, "noise": rng.normal(size=n),
                      "a_copy": a + rng.normal(scale=0.1, size=n)})
    return X, pd.Series(y)


# --------------------------------------------------------------------------- forced features

def test_forced_feature_that_fails_is_discarded_with_reason(data):
    X, y = data
    base = run_waterfall(X, y, list(X.columns), PLAN)
    twin = next(f for f in ("a", "a_copy") if f not in base.selected)   # the one clustering dropped
    res = run_waterfall(X, y, list(X.columns), PLAN, forced=["noise", twin])
    r = {f["feature"]: f for f in res.forced}
    # noise was removed by the IV filter, came back, and still fails in the model
    assert r["noise"]["previously_dropped_at"] == "Information Value"
    assert r["noise"]["result"] == "discarded"
    assert r["noise"]["reason"].startswith("forced into the model but still fails")
    # the twin duplicates a kept feature (|corr| ~0.97): forced in, it fails a model check
    assert r[twin]["previously_dropped_at"] == "Multicollinearity clustering"
    assert r[twin]["result"] == "discarded"
    assert set(res.selected) == set(base.selected)
    j = res.feature_journey().set_index("feature")
    assert j.loc["noise", "status"] == "forced → discarded"


def test_forced_feature_that_works_is_kept(data):
    X, y = data
    strict = parse_steps([{"type": "iv"}, {"type": "stepwise", "params": {"p_enter": 1e-30}},
                          {"type": "finalization"}])
    plain = run_waterfall(X, y, list(X.columns), strict)
    assert "b" not in plain.selected                              # too strict to enter
    res = run_waterfall(X, y, list(X.columns), strict, forced=["b"])
    assert "b" in res.selected and res.model.coef["b"] < 0
    assert res.forced[0]["result"] == "kept"
    assert res.feature_journey().set_index("feature").loc["b", "status"] == "forced → selected"


def test_forced_features_do_not_count_towards_max_features(data):
    X, y = data
    plan = parse_steps([{"type": "iv"}, {"type": "finalization",
                                         "params": {"max_features": 1}}])
    res = run_waterfall(X, y, ["a", "b", "c"], plan, forced=["b"])
    assert "b" in res.selected and "a" in res.selected and len(res.selected) == 2


def test_forced_bookkeeping(data):
    X, y = data
    res = run_waterfall(X, y, list(X.columns), PLAN, forced=["noise"])
    s = res.summary()
    assert (s["n_in"] + s["n_added"] - s["n_dropped"] == s["n_out"]).all()
    assert s["n_added"].iloc[-1] == 1


def test_forced_needs_finalization_and_real_columns(data):
    X, y = data
    with pytest.raises(ValueError, match="finalization"):
        run_waterfall(X, y, list(X.columns), parse_steps([{"type": "iv"}]), forced=["a"])
    with pytest.raises(ValueError, match="not in the data"):
        run_waterfall(X, y, list(X.columns), PLAN, forced=["zzz"])


# --------------------------------------------------------------------------- WoE edits

def test_woe_override_changes_woe_and_is_visible(data):
    X, y = data
    b = bin_numeric(X["a"], y)
    first = b.table["bin"].iloc[0]
    b2 = b.with_woe_overrides({first: 1.5})
    row = b2.table.iloc[0]
    assert row["woe"] == 1.5 and row["woe_calc"] == b.table["woe"].iloc[0] and row["woe_edited"]
    assert b2.iv == b.iv                                          # IV stays data-based
    assert (b2.transform(X["a"])[b.assign(X["a"]) == first] == 1.5).all()
    with pytest.raises(ValueError, match="no bin"):
        b.with_woe_overrides({"(nonsense]": 0.1})


def test_override_and_manual_bins_in_report(data):
    X, y = data
    b = bin_numeric(X["a"], y)
    lab = b.table["bin"].iloc[0]
    res = run_waterfall(X, y, list(X.columns), PLAN,
                        manual_bins={"b": {"edges": [-1, 0, 1]}},
                        woe_overrides={"a": {lab: 2.0}},
                        edit_reasons={"a": "cap the lowest bin", "b": "business cut-offs"})
    kinds = {(e["feature"], e["kind"]) for e in res.edits}
    assert kinds == {("a", "woe"), ("b", "bins")}
    md = report_markdown(res, build_id="test-1")
    assert "Finalization review" in md and "cap the lowest bin" in md and "Before:" in md
    assert "DRAFT, waiting for your confirmation" in md
    assert "WoE correlation of the final model" in md


def test_inputs_validation():
    with pytest.raises(ValueError, match="needs a reason"):
        fin.validate_inputs({"woe_overrides": {"a": {"x": 1.0}}})
    with pytest.raises(ValueError, match="exactly one"):
        fin.validate_inputs({"manual_bins": {"a": {"edges": [1], "groups": []}}})
    with pytest.raises(ValueError, match="unknown"):
        fin.validate_inputs({"forced": ["a"]})


# --------------------------------------------------------------------------- heatmap

def test_heatmap_svg(data):
    X, y = data
    res = run_waterfall(X, y, list(X.columns), PLAN)
    n = len(res.final_corr)
    svg = correlation_heatmap_svg(res.final_corr, 0.7)
    xml.dom.minidom.parseString(svg)
    assert svg.count("<title>") == n * (n + 1) // 2               # lower triangle + diagonal
    assert np.allclose(np.diag(res.final_corr), 1.0)


# --------------------------------------------------------------------------- confirmation

def test_confirmation_flow(data, tmp_path):
    X, y = data
    proj = ProjectStore(tmp_path).create("fin")
    fin.save_inputs(proj, {"forced_features": ["noise"]})
    r1 = build_scorecard(X, y, list(X.columns), ScorecardConfig(), project=proj)
    st = fin.load_status(proj)
    assert st["status"] == "pending_confirmation" and st["build_id"] == r1.build_id
    sc = json.loads((proj.dir("artifacts") / "scorecard.json").read_text())
    assert sc["meta"]["status"] == "pending_confirmation"
    assert {"woe_correlation.svg", "woe_correlation.csv", "finalization_review.md"} <= \
        {p.name for p in proj.dir("artifacts").iterdir()}

    with pytest.raises(ValueError, match="not the latest"):
        fin.confirm(proj, "wrong-id")
    fin.save_inputs(proj, {"forced_features": ["noise", "c"]})      # edit after the build
    with pytest.raises(ValueError, match="changed after this build"):
        fin.confirm(proj, r1.build_id)

    r2 = build_scorecard(X, y, list(X.columns), ScorecardConfig(), project=proj)
    assert {f["feature"] for f in r2.waterfall.forced} == {"noise", "c"}
    fin.confirm(proj, r2.build_id, note="checked with risk team")
    sc = json.loads((proj.dir("artifacts") / "scorecard.json").read_text())
    assert sc["meta"]["status"] == "confirmed"
    actions = [a["action"] for a in proj.audit_log()]
    assert actions.count("finalization_pending") == 2 and "finalization_confirmed" in actions
    assert "forced_feature" in actions


def test_finalization_api(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    monkeypatch.setattr(appmod, "store", ProjectStore(tmp_path))
    c = TestClient(appmod.app)
    pid = c.post("/api/projects", json={"name": "F"}).json()["id"]
    assert c.get(f"/api/projects/{pid}/finalization").json()["status"] is None
    bad = c.put(f"/api/projects/{pid}/finalization", json={"woe_overrides": {"a": {"x": 1}}})
    assert bad.status_code == 400
    ok = c.put(f"/api/projects/{pid}/finalization", json={"forced_features": ["a"]})
    assert ok.json()["inputs"]["forced_features"] == ["a"]
    assert c.post(f"/api/projects/{pid}/finalization/confirm",
                  json={"build_id": "x"}).status_code == 409

import math

import numpy as np
import pandas as pd
import pytest

from autoscorecard.demo_data import make_demo_data
from autoscorecard.engine import data as D
from autoscorecard.engine import metrics
from autoscorecard.engine.binning import (MISSING, OTHER, VariableBinning, bin_categorical,
                                          bin_numeric, woe_transform)
from autoscorecard.engine.logistic import fit_logit
from autoscorecard.engine.pipeline import ScorecardConfig, build_scorecard
from autoscorecard.engine.scorecard import Scaling, Scorecard
from autoscorecard.engine.selection import backward, correlation_clusters, stepwise, vif
from autoscorecard.store import ProjectStore


@pytest.fixture(scope="module")
def demo():
    t = make_demo_data(n=4000)
    df, _ = D.safe_join(t["applications"], t["customers"], "cust_id", "cust_id", "cust")
    df, _ = D.safe_join(df, t["bureau"], "cust_id", "cust_id", "bur")
    df, _ = D.safe_join(df, t["performance"], "app_id", "app_id", "perf")
    y = D.make_target(df, "perf_max_dpd_12m", "ge", 90)
    feats = [c for c in df.columns if c not in ("app_id", "cust_id", "app_date",
                                                 "perf_max_dpd_12m")]
    return df, y, feats


# --------------------------------------------------------------------------- logistic

def test_logit_single_binary_feature_matches_group_odds():
    # With one 0/1 feature the MLE is exact: b0 = log-odds of group 0,
    # b1 = log odds ratio between the groups.
    x = np.array([0] * 100 + [1] * 100)
    y = np.array([1] * 20 + [0] * 80 + [1] * 50 + [0] * 50)
    m = fit_logit(pd.DataFrame({"x": x}), y)
    assert m.converged
    assert m.coef["intercept"] == pytest.approx(math.log(20 / 80), abs=1e-8)
    assert m.coef["x"] == pytest.approx(math.log((50 / 50) / (20 / 80)), abs=1e-8)
    # textbook standard error of a log odds ratio: sqrt(1/a + 1/b + 1/c + 1/d)
    assert m.std_err["x"] == pytest.approx(math.sqrt(1 / 20 + 1 / 80 + 1 / 50 + 1 / 50), rel=1e-6)


# --------------------------------------------------------------------------- metrics

def test_auc_ks_by_hand():
    y = [0, 0, 1, 1]
    s = [0.1, 0.4, 0.35, 0.8]              # 3 of 4 bad/good pairs ordered correctly
    assert metrics.auc(y, s) == pytest.approx(0.75)
    assert metrics.gini(y, s) == pytest.approx(0.5)
    assert metrics.ks(y, s) == pytest.approx(0.5)
    assert metrics.auc(y, [-v for v in s], higher_is_riskier=False) == pytest.approx(0.75)


def test_psi_zero_for_same_distribution_and_positive_for_shift():
    rng = np.random.default_rng(0)
    a = rng.normal(size=5000)
    assert metrics.psi(a, a)[0] == pytest.approx(0.0, abs=1e-12)
    assert metrics.psi(a, a + 1.0)[0] > 0.25


def test_gains_table_sums():
    rng = np.random.default_rng(1)
    s = rng.random(1000)
    y = (rng.random(1000) < s).astype(int)
    g = metrics.gains_table(y, s, 10)
    assert g["count"].sum() == 1000 and g["bads"].sum() == y.sum()
    assert g["bad_rate"].iloc[0] > g["bad_rate"].iloc[-1]       # riskiest band first


# --------------------------------------------------------------------------- binning

def test_woe_iv_formula_on_manual_bins():
    x = pd.Series([1] * 100 + [2] * 100)
    y = [1] * 10 + [0] * 90 + [1] * 30 + [0] * 70
    b = bin_numeric(x, y, edges=[1.5])
    G, B = 160, 40
    woe1 = math.log((90 / G) / (10 / B))
    woe2 = math.log((70 / G) / (30 / B))
    assert b.table["woe"].tolist() == pytest.approx([woe1, woe2])
    iv = (90 / G - 10 / B) * woe1 + (70 / G - 30 / B) * woe2
    assert b.iv == pytest.approx(iv)


def test_numeric_binning_respects_rules(demo):
    df, y, _ = demo
    b = bin_numeric(df["cust_income"], y, min_bin_pct=0.05, max_bins=6)
    regular = b.table[b.table["bin"] != MISSING]
    assert len(regular) <= 6
    assert (regular["pct"] * len(df) >= 0.05 * len(df) - 1e-9).all()
    br = regular["bad_rate"].to_numpy()
    assert (np.diff(br) * b.direction >= 0).all()           # monotonic
    assert ((regular["goods"] > 0) & (regular["bads"] > 0)).all()
    assert b.merge_log                                        # every merge explained


def test_every_numeric_feature_is_strictly_monotonic(demo):
    df, y, feats = demo
    from autoscorecard.engine.binning import bin_all, is_numeric
    bins, _ = bin_all(df, y, [f for f in feats if is_numeric(df[f])])
    for f, b in bins.items():
        br = b.table.loc[b.table["bin"] != MISSING, "bad_rate"].to_numpy()
        assert b.is_monotonic, f
        assert (np.diff(br) * b.direction > 0).all(), f       # strict, in the chosen direction


def test_u_shape_is_forced_monotonic_and_direction_picked_by_iv():
    rng = np.random.default_rng(5)
    x = rng.uniform(-3, 3, 20000)
    y = (rng.random(20000) < 1 / (1 + np.exp(-(-2.5 + 0.25 * x ** 2 + 0.3 * x)))).astype(int)
    b = bin_numeric(pd.Series(x, name="u"), y)
    assert b.is_monotonic and b.direction == 1                # the stronger (right) arm wins
    assert b.merge_log[0].startswith("direction: ascending")


def test_manual_bins_are_kept_but_flagged_when_not_monotonic():
    x = pd.Series([1] * 100 + [2] * 100 + [3] * 100)
    y = [1] * 10 + [0] * 90 + [1] * 40 + [0] * 60 + [1] * 15 + [0] * 85
    b = bin_numeric(x, y, edges=[1.5, 2.5])
    assert b.manual and len(b.table) == 3 and not b.is_monotonic


def test_missing_bin_and_transform(demo):
    df, y, _ = demo
    b = bin_numeric(df["cust_years_at_job"], y)
    assert b.has_missing_bin and b.table["bin"].iloc[-1] == MISSING
    w = b.transform(df["cust_years_at_job"])
    miss_woe = b.table.set_index("bin").loc[MISSING, "woe"]
    assert (w[df["cust_years_at_job"].isna()] == miss_woe).all()
    # a numeric binning with no missing in training maps NaN to WoE 0
    b2 = bin_numeric(df["cust_age"], y)
    assert b2.transform(pd.Series([np.nan, 30]))[0] == 0.0


def test_categorical_rare_pooling_and_unseen():
    x = pd.Series(["a"] * 500 + ["b"] * 480 + ["c"] * 15 + ["d"] * 5)
    y = [0] * 450 + [1] * 50 + [0] * 380 + [1] * 100 + [1] * 10 + [0] * 5 + [1] * 5
    b = bin_categorical(x, y, rare_pct=0.02, min_bin_pct=0.01)
    assert any(OTHER in g for g in b.groups)
    lab = b.assign(pd.Series(["zzz", "a"]))
    other_label = next(lbl for lbl, g in zip(b.table["bin"], b.groups) if OTHER in g)
    assert lab[0] == other_label                              # unseen -> OTHER


def test_binning_roundtrip(demo):
    df, y, _ = demo
    for b in (bin_numeric(df["bur_prev_max_dpd_max"], y), bin_categorical(df["channel"], y)):
        b2 = VariableBinning.from_dict(b.to_dict())
        assert b2.transform(df[b.name]).equals(b.transform(df[b.name]))


# --------------------------------------------------------------------------- selection

def test_correlation_clusters_keep_highest_iv():
    rng = np.random.default_rng(2)
    a = rng.normal(size=2000)
    X = pd.DataFrame({"a": a, "a2": a + rng.normal(scale=0.1, size=2000),
                      "b": rng.normal(size=2000)})
    cl = correlation_clusters(X, {"a": 0.1, "a2": 0.3, "b": 0.2}, threshold=0.7)
    assert sorted(map(sorted, cl.clusters)) == [["a", "a2"], ["b"]]
    assert set(cl.representatives) == {"a2", "b"}
    assert [d["feature"] for d in cl.dropped] == ["a"]
    assert len(cl.linkage) == 2                               # n - 1 joins, full tree
    for m in ("single", "complete"):
        assert set(correlation_clusters(X, {"a": .1, "a2": .3, "b": .2}, 0.7, m)
                   .representatives) == {"a2", "b"}


def test_stepwise_and_backward_find_signal():
    rng = np.random.default_rng(3)
    n = 4000
    X = pd.DataFrame({"s1": rng.normal(size=n), "s2": rng.normal(size=n),
                      "n1": rng.normal(size=n), "n2": rng.normal(size=n)})
    y = (rng.random(n) < 1 / (1 + np.exp(2 + 0.8 * X.s1 + 0.5 * X.s2))).astype(int)
    sw = stepwise(X, y)
    assert sw.selected[:2] == ["s1", "s2"]
    assert all(sw.model.coef[f] < 0 for f in sw.selected)
    bw = backward(X, y, p_remove=0.01)
    assert {"s1", "s2"} <= set(bw.selected)


def test_vif_detects_collinearity():
    rng = np.random.default_rng(4)
    a = rng.normal(size=1000)
    X = pd.DataFrame({"a": a, "b": a + rng.normal(scale=0.05, size=1000),
                      "c": rng.normal(size=1000)})
    v = vif(X)
    assert v["a"] > 50 and v["c"] < 1.1


# --------------------------------------------------------------------------- scorecard

def test_scaling_anchor_points():
    s = Scaling(600, 50, 20)
    p_at_base = 1 / 51                                        # odds 50:1
    assert s.score_from_pd(p_at_base) == pytest.approx(600)
    assert s.score_from_pd(1 / 101) == pytest.approx(620, abs=0.3)   # odds ~doubled
    assert s.pd_from_score(600) == pytest.approx(p_at_base)


def test_points_add_up_to_scaled_model_score(demo, tmp_path):
    df, y, _ = demo
    feats = ["cust_income", "cust_age", "channel"]
    bins = {f: (bin_categorical if f == "channel" else bin_numeric)(df[f], y, name=f)
            for f in feats}
    W = woe_transform(df, bins)
    sc = Scorecard(bins, fit_logit(W, y), Scaling())
    t = sc.table()
    # unrounded points reproduce offset + factor * ln(odds_good) exactly
    raw = sum(t[t.feature == f].set_index("bin")["points_raw"]
              .reindex(bins[f].assign(df[f])).to_numpy() for f in feats)
    assert np.allclose(raw, sc.scaling.score_from_pd(sc.predict_proba(df)))
    # the rounded score is within 0.5 point per feature of that
    assert np.abs(sc.score(df).to_numpy() - raw).max() <= 0.5 * len(feats)
    sc.save(tmp_path / "sc.json")
    assert Scorecard.load(tmp_path / "sc.json").score(df).equals(sc.score(df))


# --------------------------------------------------------------------------- data

def test_safe_join_never_multiplies_rows():
    t = make_demo_data(n=500)
    out, rep = D.safe_join(t["applications"], t["bureau"], "cust_id", "cust_id", "bur")
    assert len(out) == 500 and rep.relationship.startswith("1:many")
    assert "bur_count" in out.columns
    keys = D.suggest_join_keys(t["applications"], t["customers"])
    assert keys.iloc[0][["left", "right", "relationship"]].tolist() == ["cust_id", "cust_id",
                                                                       "many:1"]


def test_target_and_split(demo):
    df, y, _ = demo
    assert set(y.unique()) == {0.0, 1.0}
    cands = D.target_candidates(df)
    assert "perf_max_dpd_12m" in cands["column"].tolist()
    s = D.split(df, y, test_size=0.3, date_col="app_date", oot_from="2024-10-01")
    assert not set(s["train"]) & set(s["test"]) and not set(s["train"]) & set(s["oot"])
    assert len(s["train"]) + len(s["test"]) + len(s["oot"]) == len(df)
    assert abs(y.loc[s["train"]].mean() - y.loc[s["test"]].mean()) < 0.01   # stratified


# --------------------------------------------------------------------------- pipeline

def test_build_scorecard_end_to_end(demo, tmp_path):
    df, y, feats = demo
    proj = ProjectStore(tmp_path).create("demo")
    res = build_scorecard(df, y, feats, ScorecardConfig(date_col="app_date",
                                                        oot_from="2024-10-01"), project=proj)
    sel = res.scorecard.features
    assert not {"noise_a", "noise_b"} & set(sel)
    assert not {"cust_income", "cust_income_band"} <= set(sel)      # one per cluster
    assert all(res.scorecard.model.coef[f] < 0 for f in sel)
    assert res.performance["test"]["gini"] > 0.3
    assert all(p < 0.1 for p in res.score_psi.values())
    files = {p.name for p in proj.dir("artifacts").iterdir()}
    assert {"scorecard.json", "scorecard_table.csv", "iv_table.csv", "clusters.json",
            "performance.json", "selection_steps.csv", "waterfall_report.md", "waterfall.svg",
            "waterfall_summary.csv", "waterfall_features.csv", "waterfall_plan.svg"} <= files
    actions = [r["action"] for r in proj.audit_log()]
    assert "drop_feature" in actions and "waterfall_step" in actions and "performance" in actions


def test_manual_bins_override(demo):
    df, y, feats = demo
    cfg = ScorecardConfig(waterfall=[], manual_bins={"cust_age": {"edges": [30, 45, 55]}})
    res = build_scorecard(df, y, ["cust_age", "channel"], cfg)
    b = res.scorecard.binnings["cust_age"]
    assert b.manual and b.table["bin"].tolist()[:4] == ["(-inf, 30]", "(30, 45]", "(45, 55]",
                                                        "(55, inf]"]

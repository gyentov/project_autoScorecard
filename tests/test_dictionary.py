import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from autoscorecard.demo_data import DEMO_DICTIONARY, make_demo_data
from autoscorecard.engine import data as D
from autoscorecard.engine.dictionary import (DataDictionary, OutlierRule, data_type,
                                             dictionary_markdown, guess_category, treat_outliers)
from autoscorecard.engine.pipeline import ScorecardConfig, build_scorecard
from autoscorecard.store import ProjectStore


@pytest.fixture(scope="module")
def demo():
    t = make_demo_data(n=4000)
    dd = DataDictionary()
    dd.update(DEMO_DICTIONARY)
    for name, tab in t.items():
        dd.add_table(name, tab)
    df = t["applications"]
    for right, on, prefix in (("customers", "cust_id", "cust"), ("bureau", "cust_id", "bur"),
                              ("performance", "app_id", "perf")):
        df, rep = D.safe_join(df, t[right], on, on, prefix, right_name=right)
        dd.add_join(rep)
    y = D.make_target(df, "perf_max_dpd_12m", "ge", 90)
    feats = [c for c in df.columns if c not in ("app_id", "cust_id", "app_date",
                                                 "perf_max_dpd_12m")]
    return df, y, feats, dd


# --------------------------------------------------------------------------- lineage

def test_join_lineage(demo):
    _, _, _, dd = demo
    e = dd.entries["bur_prev_max_dpd_mean"]
    assert e["source"] == "bureau.prev_max_dpd" and e["agg"] == "mean"
    assert e["logic"].startswith("mean of bureau.prev_max_dpd per cust_id")
    assert dd.entries["bur_count"]["logic"].startswith("number of bureau rows per cust_id")
    assert dd.entries["cust_age"]["logic"].startswith("customers.age; left join customers")


def test_descriptions_and_types(demo):
    _, _, _, dd = demo
    desc, cat, src, _ = dd.describe("bur_prev_max_dpd_max")
    assert desc == "Maximum of: worst days past due on a previous loan"   # inherited
    assert cat == "bureau / credit history" and src == "from source"
    assert dd.describe("loan_amount")[1:3] == ("loan", "user")
    assert dd.describe("bur_count")[2] == "guessed"                        # no user entry
    assert dd.describe("noise_a")[0] == ""                                 # never invented
    # the user's own logic on a source column is kept when it is joined
    assert dd.describe("cust_income_band")[3].startswith("income cut at 4, 7 and 12 million")


def test_import_with_indonesian_headers(tmp_path):
    p = tmp_path / "kamus.csv"
    pd.DataFrame({"Variabel": ["plafon"], "Deskripsi": ["Plafon kredit"],
                  "Kategori": ["loan"]}).to_csv(p, index=False)
    dd = DataDictionary()
    assert dd.import_file(p) == 1
    assert dd.describe("plafon")[:3] == ("Plafon kredit", "loan", "user")
    with pytest.raises(ValueError, match="variable"):
        dd.update([{"desc": "no name column"}])


def test_guess_and_types():
    assert guess_category("saving_balance_avg") == "funding"
    assert guess_category("loan_amount") == "loan"
    assert guess_category("zzz") is None
    assert data_type(pd.Series([1.0, 2.0, np.nan])) == "integer (stored as decimal)"
    assert data_type(pd.Series([1.5])) == "decimal"
    assert data_type(pd.to_datetime(pd.Series(["2024-01-01"]))) == "date"


def test_load_table_parses_dates(tmp_path):
    p = tmp_path / "a.csv"
    pd.DataFrame({"app_date": ["2024-01-05", "2024-02-01"], "x": [1, 2]}).to_csv(p, index=False)
    t = D.load_table(p)
    assert pd.api.types.is_datetime64_any_dtype(t["app_date"])


# --------------------------------------------------------------------------- outliers

def test_iqr_fences_by_hand():
    x = pd.Series(list(range(1, 21)) + [100.0])
    lo, hi = OutlierRule().fences(x)
    q1, q3 = x.quantile(0.25), x.quantile(0.75)
    assert (lo, hi) == (q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1))
    plo, phi = OutlierRule(method="percentile").fences(x)
    assert (plo, phi) == tuple(x.quantile([0.01, 0.99]))
    assert OutlierRule().fences(pd.Series([0, 1, 2] * 10)) is None    # low cardinality
    with pytest.raises(ValueError):
        OutlierRule(treatment="delete")


def test_cap_uses_train_fences_on_every_sample():
    df = pd.DataFrame({"x": [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 500, -500]})
    train = df.index[:12]
    out, info = treat_outliers(df, ["x"], train, OutlierRule(treatment="cap"))
    lo, hi = info["x"]["lower"], info["x"]["upper"]
    assert out["x"].max() == hi and out["x"].min() == lo
    assert info["x"]["n_high"] == 1 and info["x"]["n_low"] == 1
    same, _ = treat_outliers(df, ["x"], train, OutlierRule())       # none: untouched
    assert same["x"].equals(df["x"])


def test_extreme_values_do_not_change_the_scorecard(demo):
    """Why treatment "none" is safe: making the outliers far more extreme leaves
    the bins, WoE and points unchanged, because the end bins are open-ended."""
    df, y, feats, _ = demo
    plan = [{"type": "iv"}, {"type": "correlation_cluster"}, {"type": "stepwise"}]
    a = build_scorecard(df, y, feats, ScorecardConfig(waterfall=plan))
    wild = df.copy()
    for f in ("cust_income", "loan_amount", "bur_prev_max_dpd_mean"):
        hi = OutlierRule().fences(df[f])[1]
        wild.loc[wild[f] > hi, f] *= 1000                        # 1000x more extreme
    b = build_scorecard(wild, y, feats, ScorecardConfig(waterfall=plan))
    assert a.scorecard.features == b.scorecard.features
    ta, tb = a.scorecard.table(), b.scorecard.table()
    assert ta["points"].tolist() == tb["points"].tolist()
    assert ta["woe"].tolist() == pytest.approx(tb["woe"].tolist())


def test_capping_is_applied_before_binning(demo):
    df, y, feats, _ = demo
    plan = [{"type": "iv"}, {"type": "finalization"}]
    res = build_scorecard(df, y, ["bur_prev_max_dpd_mean", "cust_age"],
                          ScorecardConfig(waterfall=plan, outliers={"treatment": "cap"}))
    hi = res.data_dictionary.set_index("variable").loc["bur_prev_max_dpd_mean", "outlier_upper"]
    edges = res.scorecard.binnings["bur_prev_max_dpd_mean"].edges
    assert all(e < hi for e in edges[1:-1])                       # no cut beyond the cap


# --------------------------------------------------------------------------- the table

def test_dictionary_table(demo):
    df, y, feats, dd = demo
    t = dd.build(df, {f: "candidate feature" for f in feats}).set_index("variable")
    s = df["cust_years_at_job"]
    r = t.loc["cust_years_at_job"]
    assert r["count"] == len(df) and r["null_count"] == s.isna().sum()
    assert r["min"] == s.min() and r["max"] == s.max()
    assert t.loc["app_date", "data_type"] == "date"
    assert t.loc["noise_a", "description"] == "(not provided)"
    md = dictionary_markdown(t.reset_index(), OutlierRule())
    assert "Outlier detection" in md and "Outlier treatment" in md and "To complete" in md


def test_build_writes_dictionary_and_raw_heatmap(demo, tmp_path):
    df, y, feats, dd = demo
    proj = ProjectStore(tmp_path).create("dd")
    dd.save(proj)
    res = build_scorecard(df, y, feats, ScorecardConfig(), project=proj)
    files = {p.name for p in proj.dir("artifacts").iterdir()}
    assert {"data_dictionary.md", "data_dictionary.csv", "raw_correlation.svg",
            "raw_correlation.csv"} <= files
    roles = res.data_dictionary.set_index("variable")["role"]
    assert roles["bad"] == "target"
    assert set(roles[roles == "in final model"].index) == set(res.scorecard.features)
    # raw correlation: numeric model features only, Spearman
    num = [f for f in res.scorecard.features if pd.api.types.is_numeric_dtype(df[f])]
    assert list(res.raw_corr.columns) == num
    report = (proj.dir("artifacts") / "waterfall_report.md").read_text()
    assert "## Raw data" in report and "Raw-value correlation (reference only)" in report
    assert "Not shown (categorical" in report                      # channel is categorical


def test_dictionary_api(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    monkeypatch.setattr(appmod, "store", ProjectStore(tmp_path))
    c = TestClient(appmod.app)
    pid = c.post("/api/projects", json={"name": "D"}).json()["id"]
    r = c.put(f"/api/projects/{pid}/dictionary",
              json={"rows": [{"variable": "age", "description": "Age", "category": "demographic"}]})
    assert r.json()["age"]["description"] == "Age"
    c.post(f"/api/projects/{pid}/upload", files=[("files", (
        "dict.csv", b"variable,description\nincome,Monthly income\n", "text/csv"))])
    r = c.post(f"/api/projects/{pid}/dictionary/import", json={"file": "dict.csv"})
    assert r.json()["imported"] == 1 and set(r.json()["entries"]) == {"age", "income"}
    assert c.post(f"/api/projects/{pid}/dictionary/import",
                  json={"file": "../../etc/passwd"}).status_code == 404

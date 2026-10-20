import numpy as np
import pandas as pd

from autoscorecard.demo_data import make_demo_data
from autoscorecard.engine import vintage as V


def test_vintage_curves_window_and_target_from_history():
    d = make_demo_data(n=4000)
    spec = V.find_history(d, "applications")
    assert spec == {"table": "performance_monthly", "key": "app_id", "mob": "mob",
                    "snapshot": None, "dpd": "dpd"}
    c = V.vintage(d["performance_monthly"], d["applications"], spec, "app_date")
    for t in V.THRESHOLDS:                                    # cumulative: never goes down
        s = c[(c.threshold == t) & (c.cohort == "all")]["bad_rate"].to_numpy()
        assert (np.diff(s) >= -1e-9).all()
    a30 = c[(c.threshold == 30) & (c.cohort == "all")].set_index("mob")["bad_rate"]
    a90 = c[(c.threshold == 90) & (c.cohort == "all")].set_index("mob")["bad_rate"]
    a60 = c[(c.threshold == 60) & (c.cohort == "all")].set_index("mob")["bad_rate"]
    assert (a30 >= a60).all() and (a60 >= a90).all()          # ever 90+ implies ever 60+, 30+
    rec = V.recommend(c)
    assert rec["threshold"] == V.NPL_THRESHOLD                # 90+ = NPL is the default
    sc = V.share_curve(c, 90).set_index("mob")["share"]
    # the window is the inflection point: before it the curve climbs fast, after it slowly
    w = rec["window"]
    assert 3 < w < rec["horizon"] and sc[w] >= V.MIN_SHARE
    assert sc[w] - sc[w - 3] > sc[w + 3] - sc[w]
    assert V.recommend(c, 30)["threshold"] == 30              # the Boss can pick 60+ or 30+
    # early prediction: a looser threshold catches more future NPL earlier; all by the horizon
    cap = V.early_capture(d["performance_monthly"], d["applications"], spec, "app_date", rec["horizon"])
    at = cap[cap.mob == w].set_index("threshold")["capture"]
    assert at[30] >= at[60] >= at[90] and (cap[cap.mob == rec["horizon"]].capture == 1).all()
    assert (cap[cap.threshold == 90].precision.dropna() == 1).all()   # 90+ by X is NPL by the horizon

    # the 12-month 90+ flag from history equals the summary column the bank already had
    y12 = V.ever_bad(d["performance_monthly"], d["applications"], spec, "app_date", 90, 12)
    perf = d["performance"].set_index("app_id")["max_dpd_12m"]
    assert ((perf >= 90).astype(float).reindex(y12.index) == y12).all()
    # immature = not observed for the whole window -> NaN, even if already bad (no bias)
    y24 = V.ever_bad(d["performance_monthly"], d["applications"], spec, "app_date", 90, 24)
    seen = d["performance_monthly"].groupby("app_id")["mob"].max()
    assert (y24.isna() == (seen.reindex(y24.index) < 24)).all()


def test_vintage_from_snapshot_dates():
    base = pd.DataFrame({"acc": ["a", "b"], "open_date": pd.to_datetime(["2024-01-15", "2024-03-02"])})
    hist = pd.DataFrame({"acc": ["a"] * 4 + ["b"] * 3,
                         "snapshot_date": pd.to_datetime(["2024-02-28", "2024-03-31", "2024-04-30",
                                                          "2024-05-31", "2024-04-30", "2024-05-31",
                                                          "2024-06-30"]),
                         "dpd": [0, 30, 60, 90, 0, 0, 0]})
    spec = V.find_history({"loans": base, "hist": hist}, "loans")
    assert spec["snapshot"] == "snapshot_date" and spec["mob"] is None
    # a: booked mid-Jan, 30 DPD at MOB 2, 90 DPD at MOB 4; b: booked March, always current
    y = V.ever_bad(hist, base, spec, "open_date", 90, 3)
    assert y["a"] == 0 and y["b"] == 0                        # 90+ came after the window
    assert V.ever_bad(hist, base, spec, "open_date", 30, 3)["a"] == 1
    assert np.isnan(V.ever_bad(hist, base, spec, "open_date", 90, 6)["b"])   # immature


def test_knee_is_where_the_curve_bends():
    assert V.knee([1, 2, 3, 4, 5, 6], [0, .5, .8, .9, .95, 1]) == 3
    assert V.knee([1, 2, 3], [0, 0, 0]) == 3                  # flat: no bend, last point

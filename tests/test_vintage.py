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
    assert rec["threshold"] in V.THRESHOLDS and rec["window"] in V.WINDOWS
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

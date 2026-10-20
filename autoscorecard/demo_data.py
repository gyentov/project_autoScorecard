"""Synthetic multi-table credit data for tests and demos (no real people).

Tables:
    applications  one row per application: app_id, cust_id, app_date, loan terms
    customers     one row per customer: demographics
    bureau        many rows per customer: previous loans with max DPD
    performance   one row per application: max_dpd_12m (the outcome)
    performance_monthly  many rows per application: DPD at each month on book (mob 1..24,
                  observed up to 2025-12), consistent with max_dpd_12m, plus some
                  defaults after month 12; this is what vintage analysis reads

Bad risk is driven by a known hidden score, so tests can check that the engine
finds the right drivers. `income` and `income_band` are deliberately correlated,
`noise_*` columns carry no signal, `spouse_income` is mostly missing and
`is_staff_loan` is almost always 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# What a bank's own data dictionary might say. Keyed by source column name.
# noise_a / noise_b are left out on purpose: the documentation flags them.
DEMO_DICTIONARY = [
    {"variable": "app_id", "description": "Application number", "category": "identifier"},
    {"variable": "cust_id", "description": "Customer number (CIF)", "category": "identifier"},
    {"variable": "app_date", "description": "Date the application was submitted",
     "category": "application"},
    {"variable": "loan_amount", "description": "Requested loan amount (IDR)", "category": "loan"},
    {"variable": "tenor_months", "description": "Requested tenor in months", "category": "loan"},
    {"variable": "channel", "description": "Channel the application came through",
     "category": "application"},
    {"variable": "is_staff_loan", "description": "1 if the applicant is a bank employee",
     "category": "loan"},
    {"variable": "age", "description": "Age at application (years)", "category": "demographic"},
    {"variable": "income", "description": "Declared monthly income (IDR)",
     "category": "income & employment"},
    {"variable": "income_band", "description": "Declared monthly income band",
     "category": "income & employment", "logic": "income cut at 4, 7 and 12 million IDR"},
    {"variable": "marital_status", "description": "Marital status", "category": "demographic"},
    {"variable": "province", "description": "Province of residence", "category": "demographic"},
    {"variable": "years_at_job", "description": "Years with current employer",
     "category": "income & employment"},
    {"variable": "spouse_income", "description": "Declared monthly income of spouse (IDR)",
     "category": "income & employment"},
    {"variable": "prev_amount", "description": "Amount of a previous loan at any bank (IDR)",
     "category": "bureau / credit history"},
    {"variable": "prev_max_dpd", "description": "Worst days past due on a previous loan",
     "category": "bureau / credit history"},
    {"variable": "max_dpd_12m", "description": "Worst days past due in the 12 months after "
     "booking", "category": "target / performance"},
    {"variable": "mob", "description": "Months on book (1 = first month after booking)",
     "category": "target / performance"},
    {"variable": "dpd", "description": "Days past due at the end of that month on book",
     "category": "target / performance"},
]

AS_OF = pd.Timestamp("2025-12-31")          # the last month the demo performance is observed


def monthly_history(apps: pd.DataFrame, max_dpd: np.ndarray, seed: int) -> pd.DataFrame:
    """DPD per month on book. Bad accounts climb 30 days a month to their 12-month worst;
    mild ones have one late month; ~3% of the others default after month 12."""
    rng = np.random.default_rng(seed)
    months = ((AS_OF.year - apps["app_date"].dt.year) * 12
              + AS_OF.month - apps["app_date"].dt.month).clip(upper=24).to_numpy()
    ids, mobs, dpds = [], [], []
    for app_id, avail, worst in zip(apps["app_id"], months, max_dpd):
        path = np.zeros(avail, dtype=int)
        if worst >= 90:                                   # reaches its worst within 12 months
            steps = int(np.ceil(worst / 30))              # months of climbing to the worst
            peak = int(rng.integers(steps, 13))           # the month it is at its worst
            for m in range(1, avail + 1):
                if m <= peak:                             # 30 days more each month
                    path[m - 1] = max(worst - 30 * (peak - m), 0)
                elif m <= 12:
                    path[m - 1] = worst
                else:                                     # still unpaid after the first year
                    path[m - 1] = min(worst + 30 * (m - 12), 360)
        elif worst > 0:                                   # one late month, then cured
            path[int(rng.integers(0, 12))] = worst
        elif avail >= 15 and rng.random() < 0.035:        # defaults after the first year:
            first = min(10 + int(rng.geometric(0.15)), avail - 2)   # 90+ only after month 12
            for j in range(avail - first + 1):
                path[first - 1 + j] = min(30 * (j + 1), 360)
        ids += [app_id] * avail
        mobs += list(range(1, avail + 1))
        dpds += path.tolist()
    return pd.DataFrame({"app_id": ids, "mob": mobs, "dpd": dpds})


def make_demo_data(n: int = 6000, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    n_cust = int(n * 0.9)
    cust_ids = np.array([f"C{i:06d}" for i in range(n_cust)])

    age = rng.integers(21, 65, n_cust)
    income = np.round(rng.lognormal(15.6, 0.5, n_cust), -4)            # IDR / month
    customers = pd.DataFrame({
        "cust_id": cust_ids,
        "age": age,
        "income": income,
        "income_band": pd.cut(income, [0, 4e6, 7e6, 12e6, np.inf],
                              labels=["<4jt", "4-7jt", "7-12jt", ">12jt"]).astype(str),
        "marital_status": rng.choice(["single", "married", "divorced"], n_cust, p=[.4, .5, .1]),
        "province": rng.choice(["DKI", "Jabar", "Jatim", "Jateng", "Banten", "Bali", "Papua"],
                               n_cust, p=[.25, .25, .2, .15, .1, .04, .01]),
        "years_at_job": np.where(rng.random(n_cust) < 0.08, np.nan,
                                 np.round(rng.exponential(5, n_cust), 1)),
    })

    app_cust = rng.choice(cust_ids, n)
    apps = pd.DataFrame({
        "app_id": [f"A{i:07d}" for i in range(n)],
        "cust_id": app_cust,
        "app_date": pd.to_datetime("2023-01-01")
        + pd.to_timedelta(rng.integers(0, 730, n), unit="D"),
        "loan_amount": np.round(rng.lognormal(16.5, 0.6, n), -5),
        "tenor_months": rng.choice([6, 12, 18, 24, 36], n),
        "channel": rng.choice(["branch", "agent", "online"], n, p=[.5, .3, .2]),
        "noise_a": rng.normal(size=n),
        "noise_b": rng.choice(list("WXYZ"), n),
    })

    # bureau: 0-6 previous loans per customer
    n_prev = rng.poisson(2, n_cust).astype(np.intp)    # repeat counts: intp, 32-bit in the browser
    b_cust = np.repeat(cust_ids, n_prev)
    hist_risk = rng.normal(size=n_cust)                                 # hidden
    b_risk = np.repeat(hist_risk, n_prev)
    bureau = pd.DataFrame({
        "cust_id": b_cust,
        "prev_amount": np.round(rng.lognormal(15.5, 0.7, len(b_cust)), -4),
        "prev_max_dpd": np.clip(np.round(rng.exponential(np.exp(1.5 + 0.9 * b_risk))), 0, 360),
    })

    # hidden PD
    c = customers.set_index("cust_id").loc[app_cust]
    h = pd.Series(hist_risk, index=cust_ids).loc[app_cust].to_numpy()
    logit = (-2.6
             - 0.035 * (c["age"].to_numpy() - 40)
             - 0.9 * (np.log(c["income"].to_numpy()) - 15.6)
             + 0.8 * h
             + 0.35 * (np.log(apps["loan_amount"].to_numpy()) - 16.5)
             + 0.02 * (apps["tenor_months"].to_numpy() - 18)
             + np.where(apps["channel"] == "online", 0.5, 0.0)
             + np.where(c["marital_status"].to_numpy() == "divorced", 0.3, 0.0))
    pd_true = 1 / (1 + np.exp(-logit))
    bad = rng.random(n) < pd_true
    max_dpd = np.where(bad, rng.integers(90, 181, n),
                       np.where(rng.random(n) < 0.15, rng.integers(1, 60, n), 0))
    performance = pd.DataFrame({"app_id": apps["app_id"], "max_dpd_12m": max_dpd})

    # Extra columns use their own generator so the data above stays identical.
    rng2 = np.random.default_rng(seed + 1000)
    # only filled for married customers -> about half missing
    customers["spouse_income"] = np.where(customers["marital_status"] == "married",
                                          np.round(rng2.lognormal(15.3, 0.6, n_cust), -4), np.nan)
    apps.insert(6, "is_staff_loan", (rng2.random(n) < 0.02).astype(int))   # near-constant

    return {"applications": apps, "customers": customers, "bureau": bureau,
            "performance": performance,
            "performance_monthly": monthly_history(apps, max_dpd, seed + 2000)}

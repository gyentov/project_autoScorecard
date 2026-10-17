"""Build a scorecard on synthetic data, end to end, as a real project.

    .venv/bin/python scripts/demo_scorecard.py           # build a demo project
    .venv/bin/python scripts/demo_scorecard.py --docs    # also refresh docs/examples/

Writes the demo tables to projects/<id>/raw/, joins them, shows the waterfall
plan, approves it, builds the scorecard and leaves every table in
projects/<id>/artifacts/ plus the audit log.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd

from autoscorecard.config import load_settings
from autoscorecard.demo_data import DEMO_DICTIONARY, make_demo_data
from autoscorecard.engine import data as D
from autoscorecard.engine import finalization as fin
from autoscorecard.engine.dictionary import DataDictionary
from autoscorecard.engine.pipeline import ScorecardConfig, build_scorecard
from autoscorecard.engine.waterfall import approve_plan, parse_steps, save_plan
from autoscorecard.engine.waterfall_report import plan_markdown
from autoscorecard.store import ProjectStore

pd.set_option("display.width", 160)


DOCS_EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", action="store_true", help="copy the reports to docs/examples/")
    args = ap.parse_args()
    settings = load_settings()
    store = ProjectStore(settings.app.projects_dir)
    proj = store.create("Demo - synthetic loans")
    for name, t in make_demo_data().items():
        t.to_csv(proj.dir("raw") / f"{name}.csv", index=False)
    pd.DataFrame(DEMO_DICTIONARY).to_csv(proj.dir("raw") / "data_dictionary.csv", index=False)
    tables = {p.stem: D.load_table(p) for p in proj.raw_files() if p.stem != "data_dictionary"}

    # data dictionary: the bank's own descriptions, plus lineage recorded by the joins
    dictionary = DataDictionary()
    print(f"dictionary: {dictionary.import_file(proj.dir('raw') / 'data_dictionary.csv')} "
          "descriptions imported")
    for name, t in tables.items():
        dictionary.add_table(name, t)

    df = tables["applications"]
    for right, l_on, r_on, prefix in (("customers", "cust_id", "cust_id", "cust"),
                                      ("bureau", "cust_id", "cust_id", "bur"),
                                      ("performance", "app_id", "app_id", "perf")):
        df, rep = D.safe_join(df, tables[right], l_on, r_on, prefix, right_name=right)
        dictionary.add_join(rep)
        proj.audit("engine", "join", {"right": right, "on": l_on, **vars(rep)})
        print(f"join {right:<12} {rep.relationship:<20} matched {rep.matched_pct:.1%}")

    y = D.make_target(df, "perf_max_dpd_12m", "ge", 90)
    dictionary.set("bad", description="Bad flag: the outcome the scorecard predicts",
                   category="target / performance",
                   logic="1 if perf_max_dpd_12m >= 90 (90+ days past due within 12 months), "
                         "else 0")
    dictionary.save(proj)
    proj.audit("engine", "target", {"column": "perf_max_dpd_12m", "rule": ">= 90"})
    feats = [c for c in df.columns if c not in ("app_id", "cust_id", "app_date",
                                                 "perf_max_dpd_12m")]

    # the plan is shown and approved before the model is built
    steps = parse_steps(settings.modelling.waterfall)
    save_plan(proj, steps, by="system")
    print("\n" + plan_markdown(steps))
    approve_plan(proj, by="demo-script")

    # finalization inputs a modeller might give: features to try anyway, business bins
    fin.save_inputs(proj, {
        "forced_features": ["cust_province", "cust_years_at_job"],
        "manual_bins": {"cust_age": {"edges": [25, 35, 45, 55]}},
        "reasons": {"cust_province": "regional policy asks for it",
                    "cust_years_at_job": "underwriters rely on it",
                    "cust_age": "align with the policy age bands"}}, by="demo-script")
    res = build_scorecard(df, y, feats, ScorecardConfig(date_col="app_date",
                                                        oot_from="2024-10-01",
                                                        outliers=settings.modelling.outliers),
                          project=proj, require_approved=True)

    print("\nWaterfall\n", res.waterfall.summary()[["step", "n_in", "n_added", "n_dropped", "n_out"]]
          .to_string(index=False))
    print("\nForced features")
    for r in res.waterfall.forced:
        print(f"  {r['feature']:<20} {r['result']:<10} {r['reason']}")
    print("\nModel\n", res.scorecard.model.summary().round(4))
    print("\nPerformance")
    for k, v in res.performance.items():
        print(f"  {k:<6} n={v['n']:<5} bad={v['bad_rate']:.1%}  gini={v['gini']:.3f}  ks={v['ks']:.3f}")
    print("  score PSI:", {k: round(v, 4) for k, v in res.score_psi.items()})
    print(f"\nArtifacts: {proj.dir('artifacts')}")
    print(f"Build {res.build_id} is a DRAFT. Review finalization_review.md, then confirm with\n"
          f"  POST /api/projects/{proj.id}/finalization/confirm "
          f'{{"build_id": "{res.build_id}"}}')
    if args.docs:
        DOCS_EXAMPLES.mkdir(parents=True, exist_ok=True)
        for name in ("waterfall_report.md", "waterfall.svg", "waterfall_plan.svg",
                     "waterfall_plan.md", "scorecard_table.csv", "waterfall_features.csv",
                     "woe_correlation.svg", "finalization_review.md", "data_dictionary.md",
                     "data_dictionary.csv", "raw_correlation.svg"):
            shutil.copy(proj.dir("artifacts") / name, DOCS_EXAMPLES / name)
        print(f"Docs examples refreshed: {DOCS_EXAMPLES}")


if __name__ == "__main__":
    main()

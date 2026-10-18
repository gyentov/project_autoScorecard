"""End-to-end scorecard build: split -> modelling waterfall -> scale -> validate.

The feature-selection part is the waterfall (see waterfall.py): an ordered,
configurable list of steps. Every decision goes to the audit log, and when a
Project is given, the scorecard and the waterfall report are written to its
artifacts/ folder.

The Modeller agent (Phase 2) shows the waterfall plan to the user first and
calls `build_scorecard(..., require_approved=True)` once it is approved.
"""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from . import metrics
from .binning import is_numeric
from .data import split
from .dictionary import DataDictionary, OutlierRule, dictionary_markdown, treat_outliers
from .finalization import load_inputs, validate_inputs, write_pending
from .scorecard import Scaling, Scorecard
from .selection import vif
from .tuning import TuningResult, tune, with_params
from .waterfall import (DEFAULT_WATERFALL, Step, WaterfallResult, load_plan, parse_steps,
                        run_waterfall)
from .waterfall_report import (correlation_heatmap_svg, finalization_markdown, plan_markdown,
                               plan_svg, report_markdown, waterfall_svg)

DEFAULT_OUTLIERS = {"method": "iqr", "k": 1.5, "treatment": "none"}


@dataclass
class ScorecardConfig:
    # binning
    n_init: int = 20
    min_bin_pct: float = 0.05
    max_bins: int = 8
    monotonic: str | None = "auto"
    rare_pct: float = 0.01
    # feature selection: ordered waterfall steps, [{"type": ..., "params": {...}}, ...]
    waterfall: list[dict] = field(default_factory=lambda: [dict(s) for s in DEFAULT_WATERFALL])
    # split
    test_size: float = 0.3
    seed: int = 42
    date_col: str | None = None
    oot_from: str | None = None
    # scaling
    base_score: float = 600
    base_odds: float = 50
    pdo: float = 20
    # finalization inputs (a project's work/finalization.json takes precedence):
    # outliers: detection method and treatment (none | cap), see dictionary.OutlierRule
    outliers: dict = field(default_factory=lambda: dict(DEFAULT_OUTLIERS))
    # manual bins: feature -> {"edges": [...]} or {"groups": [[...], ...]}
    manual_bins: dict[str, dict] = field(default_factory=dict)
    woe_overrides: dict[str, dict[str, float]] = field(default_factory=dict)
    forced_features: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)
    # hyperparameter tuning before the waterfall (the final model): None = off, else
    # {"grid": {...}, "val_size": 0.25, "max_gap": 0.05}; see tuning.py
    tune: dict | None = None


@dataclass
class BuildResult:
    scorecard: Scorecard
    waterfall: WaterfallResult
    performance: dict[str, dict]
    score_psi: dict[str, float]
    vif: pd.Series
    splits: dict[str, pd.Index]
    build_id: str = ""
    inputs: dict = field(default_factory=dict)       # finalization inputs used
    data_dictionary: pd.DataFrame = field(default_factory=pd.DataFrame)
    raw_corr: pd.DataFrame = field(default_factory=pd.DataFrame)   # Spearman, reference
    outlier_rule: OutlierRule = field(default_factory=OutlierRule)
    tuning: TuningResult | None = None

    @property
    def iv_table(self) -> pd.DataFrame:
        j = self.waterfall.feature_journey()
        j["iv"] = j.feature.map({f: b.iv for f, b in self.waterfall.binnings.items()})
        return j

    @property
    def selection_steps(self) -> pd.DataFrame:
        return self.waterfall.selection_steps


class _Log:
    """Collects audit records; writes them to the project audit log if there is one,
    and passes them to `progress` (the office turns them into live events)."""

    def __init__(self, project=None, progress=None):
        self.project = project
        self.progress = progress
        self.records: list[dict] = []

    def __call__(self, action: str, detail: dict | None = None, reason: str | None = None):
        rec = {"action": action, "detail": detail or {}, "reason": reason}
        self.records.append(rec)
        if self.project is not None:
            self.project.audit("engine", action, detail, reason)
        if self.progress is not None:
            self.progress(action, detail or {}, reason)


def build_scorecard(df: pd.DataFrame, y: pd.Series, features: list[str],
                    cfg: ScorecardConfig | None = None, project=None,
                    require_approved: bool = False,
                    dictionary: DataDictionary | None = None,
                    progress=None, snapshot: str | None = None) -> BuildResult:
    """Build a scorecard. With a project that has a saved waterfall plan, that plan
    is used instead of cfg.waterfall; `require_approved` refuses to run an
    unapproved plan. `dictionary` (or the project's saved one) supplies variable
    descriptions, types and lineage for the raw-data documentation.
    progress(action, detail, reason) is called for every logged step.
    snapshot: also copy this build's artifacts to artifacts/<snapshot>/ (e.g. "cut1"),
    so later builds do not overwrite them."""
    cfg = cfg or ScorecardConfig()
    log = _Log(project, progress)
    plan_spec, approved = cfg.waterfall, None
    if project is not None and (project.dir("work") / "waterfall.json").exists():
        plan = load_plan(project)
        plan_spec, approved = plan["steps"], plan["approved"]
    if require_approved and not approved:
        raise PermissionError("the waterfall plan has not been approved yet")
    steps: list[Step] = parse_steps(plan_spec)
    if project is not None and (project.dir("work") / "finalization.json").exists():
        inputs = validate_inputs(load_inputs(project))
    else:
        inputs = validate_inputs({"forced_features": cfg.forced_features,
                                  "manual_bins": cfg.manual_bins,
                                  "woe_overrides": cfg.woe_overrides, "reasons": cfg.reasons})
    build_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    log("build_start", {"build_id": build_id,
                        "config": {k: v for k, v in asdict(cfg).items() if k != "waterfall"},
                        "waterfall": [s.to_dict() for s in steps], "plan_approved": approved,
                        "finalization_inputs": inputs, "rows": len(df),
                        "features": len(features)})
    features = [f for f in features if f not in cfg.exclude]

    # 1. split
    splits = split(df, y, cfg.test_size, cfg.seed, cfg.date_col, cfg.oot_from)
    log("split", {k: len(v) for k, v in splits.items()})
    tr = splits["train"]
    raw = df

    # 2. outliers: fences from the training sample; capped only if asked
    rule = OutlierRule(**cfg.outliers)
    df, outlier_info = treat_outliers(raw, features, tr, rule)
    log("outliers", {"rule": vars(rule), "features": {
        f: o for f, o in outlier_info.items() if o["n_low"] or o["n_high"]}},
        rule.treatment_description)
    Xtr, ytr = df.loc[tr], y.loc[tr].astype(int)

    # 3. waterfall on the training sample (binning happens inside, monotonic)
    bin_kw = dict(n_init=cfg.n_init, min_bin_pct=cfg.min_bin_pct, max_bins=cfg.max_bins,
                  monotonic=cfg.monotonic, rare_pct=cfg.rare_pct)
    tuned = None
    if cfg.tune is not None:                  # on the training sample only; test/OOT untouched
        t = cfg.tune
        tuned = tune(Xtr, ytr, features, steps, bin_kw, inputs, t.get("grid"),
                     t.get("val_size", 0.25), cfg.seed, t.get("max_gap", 0.05), log=log)
        bin_kw.update({k: tuned.best[k] for k in ("min_bin_pct", "max_bins") if k in tuned.best})
        steps = with_params(steps, tuned.best.get("p_enter"))
        log("tuning", {"best": tuned.best, "default": tuned.default, "trials": len(tuned.table),
                       "n_fit": tuned.n_fit, "n_val": tuned.n_val}, tuned.rule)
    wf = run_waterfall(Xtr, ytr, features, steps, bin_kw, inputs["manual_bins"], log,
                       forced=inputs["forced_features"], woe_overrides=inputs["woe_overrides"],
                       edit_reasons=inputs["reasons"])

    # 4. scaling; the scorecard stays a draft until the finalization is confirmed
    sc = Scorecard(wf.binnings, wf.model, Scaling(cfg.base_score, cfg.base_odds, cfg.pdo),
                   meta={"build_id": build_id, "status": "pending_confirmation"})
    log("model", wf.model.to_dict())
    v = vif(pd.DataFrame({f: wf.binnings[f].transform(Xtr[f]) for f in wf.selected}))

    # 5. validation on every sample; the score is higher-is-safer
    perf, psi = {}, {}
    train_score = sc.score(Xtr)
    for name, idx in splits.items():
        s = sc.score(df.loc[idx])
        perf[name] = metrics.summary(y.loc[idx].astype(int), s, higher_is_riskier=False)
        if name != "train":
            psi[name] = metrics.psi(train_score, s)[0]
    log("performance", {"performance": perf, "score_psi": psi})

    # 6. raw-data documentation and the raw-value correlation (reference only)
    if dictionary is None:
        dictionary = DataDictionary.load(project) if project is not None else DataDictionary()
    roles = {c: "not used" for c in raw.columns}
    roles.update({f: "candidate feature" for f in features})
    roles.update({f: "in final model" for f in wf.selected})
    tname = y.name or "target"
    roles[tname] = "target"
    doc_df = raw if tname in raw.columns else raw.assign(**{tname: y})
    ddict = dictionary.build(doc_df, roles, outlier_info, rule)
    num_sel = [f for f in wf.selected if is_numeric(raw[f])]
    raw_corr = Xtr[num_sel].corr(method="spearman") if num_sel else pd.DataFrame()

    res = BuildResult(sc, wf, perf, psi, v, splits, build_id, inputs, ddict, raw_corr, rule,
                      tuned)
    if project is not None:
        _save_artifacts(project, res)
        if snapshot:
            dest = project.dir("artifacts") / snapshot
            dest.mkdir(exist_ok=True)
            for f in project.dir("artifacts").iterdir():
                if f.is_file():
                    shutil.copy2(f, dest / f.name)
            log("snapshot", {"dir": str(dest), "build_id": build_id})
    return res


def _save_artifacts(project, res: BuildResult) -> None:
    out = project.dir("artifacts")
    sc, wf = res.scorecard, res.waterfall
    sc.save(out / "scorecard.json")
    sc.table().to_csv(out / "scorecard_table.csv", index=False)
    res.iv_table.to_csv(out / "iv_table.csv", index=False)
    wf.selection_steps.to_csv(out / "selection_steps.csv", index=False)
    wf.summary().to_csv(out / "waterfall_summary.csv", index=False)
    wf.feature_journey().to_csv(out / "waterfall_features.csv", index=False)
    (out / "waterfall.svg").write_text(waterfall_svg(wf.summary()), encoding="utf-8")
    (out / "waterfall_plan.svg").write_text(plan_svg(wf.steps), encoding="utf-8")
    (out / "waterfall_plan.md").write_text(plan_markdown(wf.steps), encoding="utf-8")
    corr_step = next((s for s in wf.steps if s.enabled and s.type == "correlation_cluster"), None)
    thr = corr_step.params["threshold"] if corr_step else None
    samples = {k: len(v) for k, v in res.splits.items()}
    data_md = dictionary_markdown(res.data_dictionary, res.outlier_rule, samples=samples)
    not_shown = [f for f in wf.selected if f not in res.raw_corr.columns]
    (out / "waterfall_report.md").write_text(
        report_markdown(wf, res.performance, build_id=res.build_id, corr_threshold=thr,
                        data_section=data_md, raw_corr=res.raw_corr, raw_not_shown=not_shown),
        encoding="utf-8")
    (out / "data_dictionary.md").write_text(
        dictionary_markdown(res.data_dictionary, res.outlier_rule, level=1, samples=samples,
                            title="Data dictionary"), encoding="utf-8")
    res.data_dictionary.to_csv(out / "data_dictionary.csv", index=False)
    if len(res.raw_corr) > 1:
        (out / "raw_correlation.svg").write_text(correlation_heatmap_svg(
            res.raw_corr, None, "Raw-value correlation (Spearman), reference only"),
            encoding="utf-8")
        res.raw_corr.to_csv(out / "raw_correlation.csv")
    (out / "woe_correlation.svg").write_text(correlation_heatmap_svg(wf.final_corr, thr),
                                             encoding="utf-8")
    wf.final_corr.to_csv(out / "woe_correlation.csv")
    (out / "finalization_review.md").write_text(finalization_markdown(wf, res.build_id, level=1),
                                                encoding="utf-8")
    if wf.clusters is not None:
        (out / "clusters.json").write_text(json.dumps(
            {"clusters": wf.clusters.clusters, "representatives": wf.clusters.representatives,
             "linkage": wf.clusters.linkage}, indent=2, default=float), encoding="utf-8")
    tpath = out / "tuning.json"
    if res.tuning is not None:
        tpath.write_text(json.dumps(res.tuning.to_dict(), indent=2, default=float), encoding="utf-8")
        res.tuning.table.to_csv(out / "tuning.csv", index=False)
    else:                                     # a later build without tuning: no stale result
        tpath.unlink(missing_ok=True)
        (out / "tuning.csv").unlink(missing_ok=True)
    (out / "performance.json").write_text(json.dumps(
        {"performance": res.performance, "score_psi": res.score_psi}, indent=2, default=float),
        encoding="utf-8")
    write_pending(project, res.build_id, res.inputs, {
        "selected": wf.selected,
        "forced": [{k: r[k] for k in ("feature", "result", "reason")} for r in wf.forced],
        "edits": [{"feature": e["feature"], "kind": e["kind"]} for e in wf.edits]})
    project.audit("engine", "artifacts_saved", {"dir": str(out)})

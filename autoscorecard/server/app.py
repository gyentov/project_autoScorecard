"""FastAPI backend: health, provider status, budget, projects, upload, waterfall plan,
finalization inputs and confirmation.

Run:  .venv/bin/uvicorn autoscorecard.server.app:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response, UploadFile
from pydantic import BaseModel

from autoscorecard import __version__
from autoscorecard.config import load_settings
from autoscorecard.engine import finalization as fin
from autoscorecard.engine.dictionary import DataDictionary
from autoscorecard.engine.waterfall import (STEP_TYPES, approve_plan, load_plan, parse_steps,
                                            plan_warnings, save_plan)
from autoscorecard.engine.waterfall_report import plan_markdown, plan_mermaid, plan_svg
from autoscorecard.llm import LLMRouter
from autoscorecard.store import ProjectStore

ALLOWED_EXT = {".csv", ".xlsx", ".xls", ".parquet", ".tsv", ".txt"}

settings = load_settings()
router = LLMRouter(settings)
store = ProjectStore(settings.app.projects_dir)

app = FastAPI(title="AutoScorecard", version=__version__)


class NewProject(BaseModel):
    name: str
    model_type: str = "scorecard"


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/llm/status")
def llm_status():
    return {"providers": router.status(), "agents": settings.agents,
            "fallback_chain": settings.fallback_chain}


@app.get("/api/budget")
def budget():
    b = settings.budget
    spent = router.ledger.total_usd
    return {"spent_usd": round(spent, 4), "limit_usd": b.total_usd,
            "remaining_usd": round(max(b.total_usd - spent, 0), 4),
            "by_provider": router.ledger.by_provider}


@app.get("/api/projects")
def list_projects():
    return store.list()


@app.post("/api/projects")
def create_project(body: NewProject):
    if body.model_type != "scorecard":
        raise HTTPException(400, "Only 'scorecard' is supported for now")
    return store.create(body.name, body.model_type).meta


@app.post("/api/projects/{pid}/upload")
def upload(pid: str, files: list[UploadFile]):
    try:
        proj = store.get(pid)
    except KeyError:
        raise HTTPException(404, "project not found")
    saved = []
    for f in files:
        ext = "." + (f.filename or "").rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"unsupported file type: {f.filename}")
        saved.append(proj.save_upload(f.filename or "upload", f.file).name)
    return {"saved": saved}


def _project(pid: str):
    try:
        return store.get(pid)
    except KeyError:
        raise HTTPException(404, "project not found")


@app.get("/api/projects/{pid}/audit")
def audit(pid: str):
    return _project(pid).audit_log()


# --------------------------------------------------------------------------- waterfall plan

class WaterfallPlan(BaseModel):
    steps: list[dict]


@app.get("/api/waterfall/step-types")
def waterfall_step_types():
    """Building blocks for the plan editor: every step type with defaults and help."""
    return [st.describe() for st in STEP_TYPES.values()]


@app.get("/api/projects/{pid}/waterfall")
def get_waterfall(pid: str):
    plan = load_plan(_project(pid), settings.modelling.waterfall)
    steps = parse_steps(plan["steps"])
    return {**plan, "criteria": [s.criterion for s in steps], "warnings": plan_warnings(steps),
            "mermaid": plan_mermaid(steps), "markdown": plan_markdown(steps, plan["approved"])}


@app.put("/api/projects/{pid}/waterfall")
def put_waterfall(pid: str, body: WaterfallPlan):
    """Replace the plan (reordered / edited). Saving always clears approval."""
    proj = _project(pid)
    try:
        steps = parse_steps(body.steps)
    except ValueError as e:
        raise HTTPException(400, str(e))
    save_plan(proj, steps)
    return get_waterfall(pid)


@app.post("/api/projects/{pid}/waterfall/approve")
def approve_waterfall(pid: str):
    proj = _project(pid)
    plan = load_plan(proj, settings.modelling.waterfall)
    if not (proj.dir("work") / "waterfall.json").exists():
        save_plan(proj, parse_steps(plan["steps"]), by="system")
    approve_plan(proj)
    return get_waterfall(pid)


@app.get("/api/projects/{pid}/waterfall/plan.svg")
def waterfall_plan_svg(pid: str):
    plan = load_plan(_project(pid), settings.modelling.waterfall)
    return Response(plan_svg(parse_steps(plan["steps"])), media_type="image/svg+xml")



# --------------------------------------------------------------------------- finalization

class FinalizationInputs(BaseModel):
    forced_features: list[str] = []
    manual_bins: dict[str, dict] = {}
    woe_overrides: dict[str, dict[str, float]] = {}
    reasons: dict[str, str] = {}


class Confirm(BaseModel):
    build_id: str
    note: str | None = None


@app.get("/api/projects/{pid}/finalization")
def get_finalization(pid: str):
    """Current inputs, plus the latest build's review and whether it is confirmed."""
    proj = _project(pid)
    review = proj.dir("artifacts") / "finalization_review.md"
    st = fin.load_status(proj)
    inputs = fin.load_inputs(proj)
    stale = bool(st and inputs.get("updated") and fin.inputs_hash(inputs) != st["inputs_hash"])
    return {"inputs": inputs, "status": st, "inputs_changed_since_build": stale,
            "review_markdown": review.read_text(encoding="utf-8") if review.exists() else None}


@app.put("/api/projects/{pid}/finalization")
def put_finalization(pid: str, body: FinalizationInputs):
    """Save forced features / manual edits. A rebuild is needed before confirming."""
    try:
        fin.save_inputs(_project(pid), body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_finalization(pid)


@app.post("/api/projects/{pid}/finalization/confirm")
def confirm_finalization(pid: str, body: Confirm):
    try:
        fin.confirm(_project(pid), body.build_id, note=body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return get_finalization(pid)



# --------------------------------------------------------------------------- data dictionary

class DictionaryRows(BaseModel):
    rows: list[dict]           # {"variable", "description"?, "category"?, "logic"?}


class DictionaryImport(BaseModel):
    file: str                  # a file already uploaded to the project's raw/ folder


@app.get("/api/projects/{pid}/dictionary")
def get_dictionary(pid: str):
    return DataDictionary.load(_project(pid)).entries


@app.put("/api/projects/{pid}/dictionary")
def put_dictionary(pid: str, body: DictionaryRows):
    """Add or change descriptions / variable types / logic (merged, not replaced)."""
    proj = _project(pid)
    d = DataDictionary.load(proj)
    try:
        n = d.update(body.rows)
    except ValueError as e:
        raise HTTPException(400, str(e))
    d.save(proj)
    proj.audit("user", "dictionary_updated", {"rows": n})
    return d.entries


@app.post("/api/projects/{pid}/dictionary/import")
def import_dictionary(pid: str, body: DictionaryImport):
    proj = _project(pid)
    path = proj.dir("raw") / body.file
    if not path.is_file() or path.parent != proj.dir("raw"):
        raise HTTPException(404, "file not found in this project's uploads")
    d = DataDictionary.load(proj)
    try:
        n = d.import_file(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    d.save(proj)
    proj.audit("user", "dictionary_imported", {"file": body.file, "rows": n})
    return {"imported": n, "entries": d.entries}

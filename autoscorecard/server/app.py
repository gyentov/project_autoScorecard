"""FastAPI backend: health, provider status, budget, projects, upload, waterfall plan,
finalization inputs and confirmation, and the office (meetings, inbox, events).

Run:  .venv/bin/uvicorn autoscorecard.server.app:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from autoscorecard import __version__
from autoscorecard.config import load_settings
from autoscorecard.engine import finalization as fin
from autoscorecard.engine.dictionary import DataDictionary
from autoscorecard.engine.waterfall import (STEP_TYPES, approve_plan, load_plan, parse_steps,
                                            plan_warnings, save_plan)
from autoscorecard.engine.waterfall_report import plan_markdown, plan_mermaid, plan_svg
from autoscorecard.llm import LLMRouter
from autoscorecard.office import Office
from autoscorecard.office.timeline import gantt_svg
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


# --------------------------------------------------------------------------- the office (2a)

_OFFICES: dict[str, Office] = {}


def _office(pid: str) -> Office:
    proj = _project(pid)
    if pid not in _OFFICES or _OFFICES[pid].project.root != proj.root:
        _OFFICES[pid] = Office(proj, router, settings)
    return _OFFICES[pid]


class BossSays(BaseModel):
    text: str
    meeting_id: str | None = None
    to: str | None = None          # laras / bening / citra, else routed by keywords
    cost_ok: bool = False          # the Boss agreed to Laras's estimate


class Decision(BaseModel):
    accept: bool


@app.post("/api/projects/{pid}/office/start")
def office_start(pid: str):
    return _office(pid).start()


@app.get("/api/projects/{pid}/office")
def office_state(pid: str):
    o = _office(pid)
    st = o.state
    return {"stage": st.stage, "open_meeting": st.open_meeting, "cuts": st.cuts,
            "timeline": o.timeline.to_list(),
            "unread": sum(1 for e in o.inbox.list() if not e.read),
            "pending_proposals": [vars(p) for p in o.proposals.list("pending")],
            "cost": o.cost_status()}


@app.get("/api/projects/{pid}/office/briefing")
def office_briefing(pid: str):
    return _office(pid).briefing()


@app.get("/api/projects/{pid}/office/events")
def office_events(pid: str, since: int = 0, limit: int = 500):
    """Poll with the last seq you have; the 2b office replays these."""
    return _office(pid).events.since(since, min(limit, 2000))


@app.get("/api/projects/{pid}/office/timeline.svg")
def office_timeline_svg(pid: str):
    return Response(gantt_svg(_office(pid).timeline), media_type="image/svg+xml")


@app.get("/api/projects/{pid}/inbox")
def inbox_list(pid: str):
    return [vars(e) for e in _office(pid).inbox.list()]


@app.get("/api/projects/{pid}/inbox/{eid}")
def inbox_read(pid: str, eid: str):
    try:
        return vars(_office(pid).inbox.mark_read(eid))
    except KeyError:
        raise HTTPException(404, "email not found")


@app.get("/api/projects/{pid}/files/{path:path}")
def project_file(pid: str, path: str):
    """Attachments and slides. Only files inside the project's artifacts/ and
    work/meetings/ folders, never raw data."""
    proj = _project(pid)
    f = (proj.root / path).resolve()
    allowed = [(proj.root / "artifacts").resolve(), (proj.root / "work" / "meetings").resolve()]
    if not any(f.is_relative_to(a) for a in allowed) or not f.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(f)


@app.get("/api/projects/{pid}/meetings")
def meetings_list(pid: str):
    return [{"id": m.id, "title": m.title, "kind": m.kind, "status": m.status}
            for m in _office(pid).meetings.list()]


@app.get("/api/projects/{pid}/meetings/{mid}")
def meeting_get(pid: str, mid: str):
    try:
        return vars(_office(pid).meetings.get(mid))
    except KeyError:
        raise HTTPException(404, "meeting not found")


@app.post("/api/projects/{pid}/meetings/{mid}/choose/{cid}")
def meeting_choose(pid: str, mid: str, cid: str):
    try:
        return _office(pid).choose(mid, cid)
    except KeyError:
        raise HTTPException(404, "meeting or choice not found")
    except (ValueError, PermissionError) as e:
        raise HTTPException(409, str(e))


@app.post("/api/projects/{pid}/office/say")
def office_say(pid: str, body: BossSays):
    """Free typing, in a meeting (meeting_id) or at an agent's desk."""
    try:
        return _office(pid).say(body.text, body.meeting_id, body.to, body.cost_ok)
    except KeyError:
        raise HTTPException(404, "meeting not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{pid}/proposals/{prop}")
def proposal_decide(pid: str, prop: str, body: Decision):
    try:
        return _office(pid).decide_proposal(prop, body.accept)
    except KeyError:
        raise HTTPException(404, "proposal not found")
    except ValueError as e:
        raise HTTPException(409, str(e))

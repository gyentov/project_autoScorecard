import json
import xml.dom.minidom

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from autoscorecard.config import AppConfig, Price, ProviderConfig, Settings
from autoscorecard.demo_data import DEMO_DICTIONARY, make_demo_data
from autoscorecard.llm import LLMRouter, ToolCall, Usage
from autoscorecard.llm.base import Provider
from autoscorecard.llm.cost import Ledger
from autoscorecard.llm.types import LLMResponse
from autoscorecard.office import Office
from autoscorecard.office import costs
from autoscorecard.office.agent import route
from autoscorecard.office.timeline import Timeline, gantt_svg
from autoscorecard.store import ProjectStore

AGENTS = ("partner", "prepper", "modeller", "deck_creator")


def offline_settings(tmp_path) -> Settings:
    return Settings(providers={"offline": ProviderConfig(type="offline")},
                    agents={a: "offline" for a in AGENTS}, fallback_chain=["offline"],
                    app=AppConfig(projects_dir=tmp_path / "p", ledger_path=tmp_path / "l.jsonl"))


def demo_project(tmp_path, n=3000):
    proj = ProjectStore(tmp_path / "p").create("Demo")
    for name, t in make_demo_data(n=n).items():
        t.to_csv(proj.dir("raw") / f"{name}.csv", index=False)
    pd.DataFrame(DEMO_DICTIONARY).to_csv(proj.dir("raw") / "data_dictionary.csv", index=False)
    return proj


@pytest.fixture
def office(tmp_path):
    s = offline_settings(tmp_path)
    return Office(demo_project(tmp_path), LLMRouter(s, Ledger(tmp_path / "l.jsonl")), s,
                  inline_jobs=True)


def open_meeting(o):
    return o.meetings.get(o.state.open_meeting)


# --------------------------------------------------------------------------- the whole flow

def test_full_project_flow(office):
    o = office
    o.start()
    m = open_meeting(o)
    assert m.kind == "kickoff" and [c["id"] for c in m.choices] == ["acc"]
    o.choose(m.id, "acc")
    m = open_meeting(o)
    assert m.kind == "data" and "applications" in m.script[0]["text"]
    o.choose(m.id, "acc")
    m = open_meeting(o)
    assert m.kind == "target" and "perf_max_dpd_12m" in m.script[0]["text"]
    o.choose(m.id, "acc")
    m = open_meeting(o)
    assert m.kind == "plan"
    o.choose(m.id, "acc")                                         # builds Cut 1 inline
    st = o.state
    assert st.cuts["cut1"]["status"] == "built"
    m = open_meeting(o)
    assert m.kind == "results" and m.cut == "cut1"
    assert [c["action"] for c in m.choices] == ["next_cut"]

    # typed instruction -> proposal -> ACC choice inside the meeting
    r = o.say("paksa cust_province", meeting_id=m.id)
    assert r["proposals"] and r["agent"] == "citra"
    m = o.meetings.get(m.id)
    acc = next(c for c in m.choices if c["action"] == "proposal" and c["payload"]["accept"])
    o.choose(m.id, acc["id"])
    o.choose(m.id, "next")
    m = open_meeting(o)
    assert m.cut == "cut2" and [c["action"] for c in m.choices] == ["confirm_final", "extend"]
    text = " ".join(l["text"] for l in m.script)
    assert "Paksa cust_province" in text                          # the change is named

    # extension without typed feedback is refused, with typed feedback it builds Cut 3
    assert o.choose(m.id, "extend") == {"note": "ketik masukan dulu"}
    o.say("tolong cek lagi age band", meeting_id=m.id)
    o.choose(m.id, "extend")
    m = open_meeting(o)
    assert m.cut == "cut3"
    assert "belum menjadi perubahan yang di-ACC" in " ".join(l["text"] for l in m.script)
    res = o.choose(m.id, "confirm")
    sc = json.loads((o.project.dir("artifacts") / "scorecard.json").read_text())
    assert sc["meta"]["status"] == "confirmed" and sc["meta"]["build_id"] == res["confirmed"]

    tl = {i.id: i.status for i in o.timeline.items}
    assert [k for k in tl if k.startswith("cut")] == ["cut1", "cut2", "cut3"]
    assert all(tl[k] == "done" for k in ("kickoff", "data", "target", "waterfall_plan", "cut1",
                                         "cut2", "cut3"))
    assert tl["deck"] == "not_started"
    kinds = [e.kind for e in o.inbox.list()]
    assert kinds.count("mom") == 7 and kinds.count("results") == 3
    moms = [e for e in o.inbox.list() if e.kind == "mom"]
    assert "Keputusan" in moms[-1].body_md and "dikonfirmasi final" in moms[-1].body_md
    types = {e["type"] for e in o.events.since(0)}
    assert {"meeting_scheduled", "dialogue", "job_progress", "handoff", "approval",
            "milestone", "email_sent", "proposal"} <= types
    seqs = [e["seq"] for e in o.events.since(0)]
    assert seqs == list(range(1, len(seqs) + 1))
    for c in ("cut1", "cut2", "cut3"):                            # nothing overwritten
        assert (o.project.dir("artifacts") / c / "waterfall_report.md").exists()


def test_model_respects_correlation_limit(office):
    o = office
    for kind in ("kickoff", "data", "target", "plan"):
        o.choose(o.state.open_meeting or o.start()["meeting_id"], "acc")
    corr = pd.read_csv(o.project.dir("artifacts") / "cut1" / "woe_correlation.csv", index_col=0)
    off = corr.where(~pd.DataFrame(
        [[i == j for j in range(len(corr))] for i in range(len(corr))],
        index=corr.index, columns=corr.columns)).abs()
    assert off.max().max() < 0.7


def test_no_data_means_waiting(tmp_path):
    s = offline_settings(tmp_path)
    proj = ProjectStore(tmp_path / "p").create("Empty")
    o = Office(proj, LLMRouter(s, Ledger(tmp_path / "l.jsonl")), s, inline_jobs=True)
    o.choose(o.start()["meeting_id"], "acc")
    assert o.timeline.get("data").status == "waiting_boss"
    assert o.inbox.list()[-1].subject == "Data belum ada"


# --------------------------------------------------------------------------- routing & commands

def test_route():
    assert route("@citra halo") == "citra"
    assert route("kenapa gini OOT turun?") == "citra"
    assert route("join tabel bureau pakai kolom apa?") == "bening"
    assert route("berapa biaya sisa budget?") == "laras"
    assert route("selamat pagi") == "laras"


def test_commands(office):
    o = office
    for _ in range(4):
        o.choose(o.state.open_meeting or o.start()["meeting_id"], "acc")
    r = o.agents.command("bins cust_age 25, 35 45")
    p = o.proposals.list("pending")[0]
    assert p.kind == "bin_edit" and p.payload["edges"] == [25.0, 35.0, 45.0]
    assert o.agents.command("bins cust_age 25, 35 45").proposals == r.proposals   # no dup
    miss = o.agents.command("paksa umur")
    assert not miss.proposals and "tidak menemukan" in miss.text
    assert o.agents.command("apa kabar?") is None


# --------------------------------------------------------------------------- the LLM path

class Scripted(Provider):
    def __init__(self, name, cfg, replies):
        super().__init__(name, cfg)
        self.replies, self.requests = list(replies), []

    def complete(self, req):
        self.requests.append(req)
        r = self.replies.pop(0)
        return LLMResponse(text=r.get("text", ""), tool_calls=r.get("tools", []),
                           stop="tool_calls" if r.get("tools") else "end",
                           usage=Usage(input_tokens=2000, output_tokens=200),
                           provider=self.name, model="fake")


def paid_office(tmp_path, replies, ask_above=1.0):
    cfg = ProviderConfig(type="anthropic", model="fake", price=Price(input=1, output=5))
    s = Settings(providers={"paid": cfg, "offline": ProviderConfig(type="offline")},
                 agents={a: "paid" for a in AGENTS}, fallback_chain=["paid", "offline"],
                 budget={"total_usd": 4.5, "per_project_usd": 1.0, "ask_above_usd": ask_above},
                 app=AppConfig(projects_dir=tmp_path / "p", ledger_path=tmp_path / "l.jsonl"))
    prov = Scripted("paid", cfg, replies)
    router = LLMRouter(s, Ledger(tmp_path / "l.jsonl"), providers={"paid": prov},
                       sleep=lambda _: None)
    proj = ProjectStore(tmp_path / "p").create("LLM")
    return Office(proj, router, s, inline_jobs=True), prov


def test_agent_uses_tools_mood_and_reports_cost(tmp_path):
    o, prov = paid_office(tmp_path, [
        {"tools": [ToolCall("t1", "cost_status", {})]},
        {"text": "<mood:proud> Budget masih aman, Boss."},
    ])
    r = o.say("@laras budget kita masih aman?")
    assert r["agent"] == "laras" and r["mood"] == "proud"
    assert r["text"] == "Budget masih aman, Boss."
    tool_result = prov.requests[1].messages[-1].content[0]
    assert '"budget_total_usd": 4.5' in tool_result.content and not tool_result.is_error
    spent = (2 * (2000 * 1 + 200 * 5)) / 1e6
    assert r["actual"]["actual"] == pytest.approx(spent)
    assert r["estimate"]["low_usd"] <= spent <= r["estimate"]["high_usd"]
    assert r["actual"]["within_range"]


def test_expensive_question_needs_ok_first(tmp_path):
    o, prov = paid_office(tmp_path, [{"text": "<mood:neutral> Oke."}], ask_above=0.000001)
    r = o.say("jelaskan semuanya")
    assert r["needs_cost_ok"] and "Perkiraan biaya" in r["text"] and not prov.requests
    r = o.say("jelaskan semuanya", cost_ok=True)
    assert r["text"] == "Oke." and len(prov.requests) == 1


def test_proposal_tool_never_changes_the_model(tmp_path):
    o, _ = paid_office(tmp_path, [
        {"tools": [ToolCall("t1", "propose_plan_change",
                            {"step": "iv", "params": {"min": 0.05}, "reason": "lebih ketat"})]},
        {"text": "<mood:serious> Saya usulkan IV minimal 0,05."},
    ])
    r = o.say("@citra perketat IV ya")
    assert len(r["proposals"]) == 1
    p = o.proposals.list("pending")[0]
    assert p.kind == "plan_change" and p.status == "pending"
    from autoscorecard.engine.waterfall import load_plan
    iv = next(s for s in load_plan(o.project)["steps"] if s["type"] == "iv")
    assert iv.get("params", {}).get("min") != 0.05                # unchanged until ACC
    o.decide_proposal(p.id, True)
    iv = next(s for s in load_plan(o.project)["steps"] if s["type"] == "iv")
    assert iv["params"]["min"] == 0.05


# --------------------------------------------------------------------------- costs & timeline

def test_estimate_calibration(tmp_path):
    o, _ = paid_office(tmp_path, [])
    est = costs.estimate(o.router, "partner", 4000, o.project)
    assert est.low_usd < est.high_usd and est.provider == "paid" and not est.calibration
    for _ in range(3):
        costs.record_actual(o.project, est, est.mid_usd * 2, "x")
    est2 = costs.estimate(o.router, "partner", 4000, o.project)
    assert est2.mid_usd == pytest.approx(est.mid_usd * 2) and "Dikalibrasi" in est2.say()


def test_timeline_extension_and_gantt(tmp_path):
    proj = ProjectStore(tmp_path / "p").create("T")
    tl = Timeline(proj)
    pos = tl.slots()
    assert pos["kickoff"] == (1, 1) and pos["dictionary"] == (2, 4) and pos["deck"] == (7, 7)
    tl.add_extension("Boss minta revisi")
    assert [m.id for m in tl.cuts()] == ["cut1", "cut2", "cut3"]
    assert tl.get("deck").depends_on == ["cut3"] and tl.slots()["deck"] == (8, 8)
    assert tl.get("cut3").title.endswith("(final)") and not tl.get("cut2").title.endswith("(final)")
    xml.dom.minidom.parseString(gantt_svg(tl))


# --------------------------------------------------------------------------- API

def test_office_api(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    s = offline_settings(tmp_path)
    store = ProjectStore(tmp_path / "p")
    monkeypatch.setattr(appmod, "store", store)
    monkeypatch.setattr(appmod, "settings", s)
    monkeypatch.setattr(appmod, "router", LLMRouter(s, Ledger(tmp_path / "l.jsonl")))
    appmod._OFFICES.clear()
    c = TestClient(appmod.app)
    proj = demo_project(tmp_path)
    mid = c.post(f"/api/projects/{proj.id}/office/start").json()["meeting_id"]
    m = c.get(f"/api/projects/{proj.id}/meetings/{mid}").json()
    assert m["kind"] == "kickoff" and m["slides"]
    slide = c.get(f"/api/projects/{proj.id}/files/{m['slides'][1]['file']}")
    assert slide.status_code == 200 and slide.text.startswith("<svg")
    assert c.get(f"/api/projects/{proj.id}/files/raw/applications.csv").status_code == 404
    assert c.get(f"/api/projects/{proj.id}/files/../../etc/passwd").status_code == 404
    r = c.post(f"/api/projects/{proj.id}/office/say", json={"text": "halo", "meeting_id": mid})
    assert r.json()["offline"] is True
    c.post(f"/api/projects/{proj.id}/meetings/{mid}/choose/acc")
    st = c.get(f"/api/projects/{proj.id}/office").json()
    assert st["stage"] == "data" and st["unread"] >= 2
    ev = c.get(f"/api/projects/{proj.id}/office/events", params={"since": 3}).json()
    assert ev[0]["seq"] == 4
    mail = c.get(f"/api/projects/{proj.id}/inbox").json()[0]
    assert c.get(f"/api/projects/{proj.id}/inbox/{mail['id']}").json()["read"] is True
    assert c.post(f"/api/projects/{proj.id}/meetings/{mid}/choose/acc").status_code == 409

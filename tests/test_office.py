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


def begin(o):
    """The Boss confirms the project's files with Laras, then kickoff starts."""
    o.hand_over_data([p.name for p in o.project.raw_files()])
    return o.start()


def open_meeting(o):
    return o.meetings.get(o.state.open_meeting)


# --------------------------------------------------------------------------- the whole flow

def test_full_project_flow(office):
    o = office
    assert o.start()["needs_data"]                  # Laras confirms the data before kickoff
    begin(o)
    m = open_meeting(o)
    assert m.kind == "kickoff" and [c["id"] for c in m.choices] == ["acc", "adjourn"]
    o.choose(m.id, "acc")
    m = open_meeting(o)                       # Bening joined on her own: straight to target
    assert m.kind == "target" and "vintage analysis" in m.script[0]["text"]
    t = o.state.target                           # the vintage evidence picked the definition
    assert t["column"].startswith("vin_ever") and t["vintage"]["window"] in (6, 9, 12, 18, 24)
    assert [sl["title"] for sl in m.slides][:3] == [f"Vintage {t['vintage']['threshold']}+ DPD "
                                                    "per kohort", "Kurva 30+ / 60+ / 90+",
                                                    "Rekomendasi definisi"]
    assert any("perf_max_dpd_12m" in c["label"] for c in m.choices)      # the 12-month summary
    from autoscorecard.office import prep as _prep
    df, y = o.dataset()
    feats, out = _prep.feature_list(df, t, {})
    assert "perf_max_dpd_12m" not in feats and not any(f.startswith("vin_") for f in feats)
    assert not any(c.startswith("perf_mob") or c == "perf_dpd" for c in df.columns)  # not joined
    memo = next(e for e in o.inbox.list() if e.subject == "Catatan join data")
    assert "applications" in memo.body_md and "kunci lain yang dicoba" in memo.body_md
    assert o.state.join_approved and all(j["tried"] for j in o.state.join_plan["joins"])
    o.choose(m.id, "acc")
    m = open_meeting(o)
    assert m.kind == "plan"
    o.choose(m.id, "acc")                                         # builds Cut 1 inline
    st = o.state
    assert st.cuts["cut1"]["status"] == "built"
    m = open_meeting(o)
    assert m.kind == "results" and m.cut == "cut1"
    assert [c["action"] for c in m.choices] == ["next_cut"]
    join_slide = next(sl for sl in m.slides if sl["title"] == "Join data (Bening)")
    assert "fitur di model" in (o.project.root / join_slide["file"]).read_text()
    assert any(l["speaker"] == "bening" and "join" in l["text"] for l in m.script)

    # typed instruction -> proposal -> ACC choice inside the meeting
    r = o.say("paksa cust_province", meeting_id=m.id)
    assert r["proposals"] and r["agent"] == "citra"
    m = o.meetings.get(m.id)
    acc = next(c for c in m.choices if c["action"] == "proposal" and c["payload"]["accept"])
    o.choose(m.id, acc["id"])
    o.choose(m.id, "next")
    m = open_meeting(o)
    assert m.cut == "cut2" and [c["action"] for c in m.choices] == [
        "levels_acc", "edit_levels", "confirm_final", "extend"]
    text = " ".join(l["text"] for l in m.script)
    assert "Paksa cust_province" in text                          # the change is named
    from dataclasses import asdict                                   # the API can send the state
    json.dumps(asdict(o.state), allow_nan=False)
    titles = [sl["title"] for sl in m.slides]                      # Citra's report, every cut
    assert {"Kontribusi skor", "Binning per variabel", "Pembagian risiko (20 kelompok)",
            "Usulan kategori risiko (VL–VH)"} <= set(titles)
    bands = pd.read_csv(o.project.dir("artifacts") / "cut2" / "risk_bands.csv")
    assert (bands["pct"] >= 0.05).all() and len(bands) <= 20
    lv = o.risk_levels("cut2")["levels"]
    assert lv["labels"][0] == "VH" and lv["labels"][-1] == "VL" and not lv["approved"]
    with pytest.raises(ValueError):                               # VL before VH: refused
        o.edit_risk_levels("cut2", ["VL"] + lv["labels"][1:])
    o.edit_risk_levels("cut2", ["VH"] * 2 + lv["labels"][2:])
    assert o.risk_levels("cut2")["levels"]["approved"]
    assert (o.project.dir("artifacts") / "cut2" / "risk_levels.csv").exists()
    assert "Kategori risiko sekarang" in o.meetings.get(m.id).transcript[-1]["text"]

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
    assert tl["deck"] == "waiting_boss"                           # draft deck made at confirmation
    deck = (o.project.dir("artifacts") / "deliverables" / "deck_draft.html").read_text()
    for part in ("Executive summary", "Background", "Bad definition", "Vintage analysis",
                 "Model development process", "Model waterfall", "Recommended score binning",
                 "Risk levels", "Scorecard by variable", "Recommendation"):
        assert f">{part}" in deck, part                           # the consulting structure
    assert deck.count(">Scorecard by variable<") == len(o.state.cuts["cut3"]["features"])
    assert "nan" not in deck.lower().replace("nance", "")       # no raw NaN leaks into the text
    kinds = [e.kind for e in o.inbox.list()]
    assert kinds.count("mom") == 6 and kinds.count("results") == 5   # 3 cuts + documentation + deck
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
    for kind in ("kickoff", "target", "plan"):
        o.choose(o.state.open_meeting or begin(o)["meeting_id"], "acc")
    corr = pd.read_csv(o.project.dir("artifacts") / "cut1" / "woe_correlation.csv", index_col=0)
    off = corr.where(~pd.DataFrame(
        [[i == j for j in range(len(corr))] for i in range(len(corr))],
        index=corr.index, columns=corr.columns)).abs()
    assert off.max().max() < 0.7


def test_no_data_means_waiting(tmp_path):
    s = offline_settings(tmp_path)
    proj = ProjectStore(tmp_path / "p").create("Empty")
    o = Office(proj, LLMRouter(s, Ledger(tmp_path / "l.jsonl")), s, inline_jobs=True)
    r = o.start()                                   # no data: Laras asks for it, nothing starts
    assert r["needs_data"] and "belum ada" in r["note"] and o.state.stage == "new"
    assert "upload" in " ".join(l["text"] for l in o.briefing())
    for name, t in make_demo_data(n=1500).items():
        t.to_csv(proj.dir("raw") / f"{name}.csv", index=False)
    assert "konfirmasi" in o.start()["note"]          # files exist but are not confirmed yet
    o.choose(begin(o)["meeting_id"], "acc")
    assert open_meeting(o).kind == "target" and o.data_files()["locked"]


# --------------------------------------------------------------------------- routing & commands

def test_route():
    assert route("@citra halo") == "citra"
    assert route("kenapa gini OOT turun?") == "citra"
    assert route("join tabel bureau pakai kolom apa?") == "bening"
    assert route("berapa biaya sisa budget?") == "laras"
    assert route("selamat pagi") == "laras"


def test_commands(office):
    o = office
    for _ in range(3):                            # kickoff, target, plan -> Cut 1
        o.choose(o.state.open_meeting or begin(o)["meeting_id"], "acc")
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
    monkeypatch.setattr(Office, "WORK_PAUSE", 0.0)       # desk work runs in a thread: no wait
    c = TestClient(appmod.app)
    proj = demo_project(tmp_path)
    files = [f["name"] for f in c.get(f"/api/projects/{proj.id}/data").json()["files"]]
    c.post(f"/api/projects/{proj.id}/data/handover", json={"files": files})
    mid = c.post(f"/api/projects/{proj.id}/office/start").json()["meeting_id"]
    m = c.get(f"/api/projects/{proj.id}/meetings/{mid}").json()
    assert m["kind"] == "kickoff" and m["slides"]
    slide = c.get(f"/api/projects/{proj.id}/files/{m['slides'][1]['file']}")
    assert slide.status_code == 200 and slide.text.startswith("<svg")
    assert c.get(f"/api/projects/{proj.id}/files/raw/applications.csv").status_code == 404
    assert c.get(f"/api/projects/{proj.id}/files/../../etc/passwd").status_code == 404
    r = c.post(f"/api/projects/{proj.id}/office/say", json={"text": "halo", "meeting_id": mid})
    assert r.json()["offline"] is False and r.json()["estimate"] is None   # $0 small talk
    r = c.post(f"/api/projects/{proj.id}/office/say",
               json={"text": "kenapa targetnya dpd 90?", "meeting_id": mid})
    assert r.json()["offline"] is True
    c.post(f"/api/projects/{proj.id}/meetings/{mid}/choose/acc")
    for _ in range(100):                                  # Bening works at her desk first
        st = c.get(f"/api/projects/{proj.id}/office").json()
        if st["open_meeting"]:
            break
        import time
        time.sleep(0.05)
    assert st["stage"] == "target" and st["unread"] >= 2       # join done without a meeting
    ev = c.get(f"/api/projects/{proj.id}/office/events", params={"since": 3}).json()
    assert ev[0]["seq"] == 4
    mail = c.get(f"/api/projects/{proj.id}/inbox").json()[0]
    assert c.get(f"/api/projects/{proj.id}/inbox/{mail['id']}").json()["read"] is True
    assert c.post(f"/api/projects/{proj.id}/meetings/{mid}/choose/acc").status_code == 409


def test_script_lines_carry_a_pose():
    from autoscorecard.office.meetings import pose_for, say
    assert say("laras", "proud", "x")["pose"] == "presenting"
    assert say("laras", "neutral", "x", slide=0)["pose"] == "presenting"
    assert say("laras", "neutral", "x")["pose"] == "neutral"
    assert say("citra", "serious", "x", pose="tablet")["pose"] == "tablet"
    assert pose_for("apologetic") == "bowing"
    with pytest.raises(ValueError):
        say("laras", "neutral", "x", pose="dancing")


def test_small_talk_is_free_and_in_character(tmp_path):
    from autoscorecard.office.characters import small_talk
    assert small_talk("Halo!", "citra")[1].startswith("Halo, Boss")
    assert small_talk("makasih ya", "kirana") is not None
    assert small_talk("halo, kenapa gini OOT turun padahal fitur sama?", "citra") is None  # a real question
    o = Office(demo_project(tmp_path), LLMRouter(offline_settings(tmp_path),
                                                 Ledger(tmp_path / "l.jsonl")), offline_settings(tmp_path))
    r = o.say("pagi", to="bening", candid=True)
    assert r["agent"] == "bening" and r["estimate"] is None and not r["offline"]


def test_demo_endpoint_and_ui_are_served(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    monkeypatch.setattr(appmod, "store", ProjectStore(tmp_path / "p"))
    c = TestClient(appmod.app)
    p = c.post("/api/demo").json()
    assert p["name"].startswith("Demo") and c.get(f"/api/projects/{p['id']}/audit").status_code == 200
    assert "AutoScorecard Office" in c.get("/").text
    L = c.get("/assets/office/layout.json").json()
    # every seat and spot the browser walks to is on the floor and reachable from the door
    free = {(x, y) for y, row in enumerate(L["solid"]) for x, ch in enumerate(row) if ch == "."}
    seen, todo = set(), [(L["entrance"]["x"], L["entrance"]["y"])]
    while todo:
        x, y = todo.pop()
        if (x, y) in free and (x, y) not in seen:
            seen.add((x, y))
            todo += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    P = L["pantry"]
    spots = [*L["desks"].values(), L["boss_desk"], L["mailbox"], L["whiteboard"], L["greeter"],
             L["escort"], L["meeting"]["door"], L["meeting"]["host"], *L["meeting"]["seats"],
             P["dispenser"], P["coffee"], P["microwave"], P["vending"], *P["table"],
             *L["lounge"], *L["huddle"]]
    assert all((s["x"], s["y"]) in seen for s in spots)
    assert c.get("/assets/sprites/laras.png").status_code == 200
    man = c.get("/assets/characters/manifest.json").json()
    assert "laras/neutral_neutral.png" in man["files"]["laras"]


def test_inner_thoughts_never_reach_the_boss_but_are_logged(tmp_path):
    o, _ = paid_office(tmp_path, [
        {"text": "Boss tanya soal data. Sebagai Laras aku harus klarifikasi.\n"
                 "<mood:thinking><batin>Jangan overthink.</batin>"
                 "<ucap>Boss, maksudnya file data untuk proyek ini?</ucap>"},
    ])
    r = o.say("@laras bagaimana cara menaruh data?", to="laras", candid=True)
    assert r["text"] == "Boss, maksudnya file data untuk proyek ini?" and r["mood"] == "thinking"
    assert "thought" not in r
    shown = [e for e in o.events.since(0) if e["type"] == "dialogue" and e["actor"] == "laras"]
    assert "Sebagai Laras" not in json.dumps(shown) and "overthink" not in json.dumps(shown)
    plain = o.conversations.list()
    assert plain[-1]["has_thought"] and "thought" not in plain[-1]
    assert plain[-1]["usd"] > 0 and plain[-1]["boss"].startswith("@laras")
    audit = o.conversations.list(audit=True)[-1]["thought"]
    assert "Sebagai Laras" in audit and "Jangan overthink." in audit


def test_data_handover_selects_files_and_locks_after_kickoff(office):
    files = {f["name"]: f for f in office.data_files()["files"]}
    assert files["data_dictionary.csv"]["kind"] == "kamus" and all(f["selected"] for f in files.values())
    assert all(f["rows"] > 0 for f in files.values())
    tables = [n for n, f in files.items() if f["kind"] == "tabel"]
    keep = [tables[0], "data_dictionary.csv"]
    r = office.hand_over_data(keep)
    assert "1 tabel" in r["text"] and "Bening" in r["text"]
    from autoscorecard.office import prep
    got, dicts = prep.split_uploads(office.project)
    assert list(got) == [tables[0].rsplit(".", 1)[0]] and len(dicts) == 1
    ev = [e["type"] for e in office.events.since(0)]
    assert "data_received" in ev and "handoff" in ev
    assert office.conversations.list()[-1]["kind"] == "handover"
    with pytest.raises(KeyError):
        office.hand_over_data(["nope.csv"])
    office.hand_over_data(sorted(files))            # still open before the data stage
    office.start()
    office.choose(office.state.open_meeting, "acc")  # -> data stage: now locked
    assert office.data_files()["locked"]
    with pytest.raises(ValueError):
        office.hand_over_data(keep)


def test_meetings_can_be_adjourned_without_acc(office):
    o = office
    m = o.meetings.get(begin(o)["meeting_id"])
    assert "ketik masukan" in o.choose(m.id, "adjourn")["note"]     # nothing to work on yet
    o.say("timeline-nya terlalu panjang, persingkat", meeting_id=m.id)
    o.choose(m.id, "adjourn")
    assert o.meetings.get(m.id).status == "ended"
    m2 = open_meeting(o)
    assert m2.id != m.id and m2.kind == "kickoff"
    first = m2.script[0]["text"]                     # summarised, not quoted word for word
    assert "lanjutan" in first and "timeline" in first and "terlalu panjang" not in first
    assert o.state.feedback["kickoff"] == ["timeline-nya terlalu panjang, persingkat"]
    mom = [e for e in o.inbox.list() if e.kind == "mom"][-1]
    assert "Belum di-ACC" in mom.body_md and "Arahan Boss (ringkas)" in mom.body_md
    assert "terlalu panjang" not in mom.body_md                  # the exact words: log only
    assert o.conversations.list()[-1]["boss"] == "timeline-nya terlalu panjang, persingkat"
    # a target the Boss ACC'd is kept when the target meeting is redone
    o.choose(m2.id, "acc")
    t = open_meeting(o)
    o.proposals.add("target", {"column": "perf_max_dpd_12m", "rule": "ge", "value": 60}, "bening",
                    "Boss")
    p = o.proposals.list("pending")[0]
    o.decide_proposal(p.id, True)
    o.say("pakai dpd 60 saja", meeting_id=t.id)
    o.choose(t.id, "adjourn")
    assert o.state.target["value"] == 60 and open_meeting(o).kind == "target"


def test_projects_go_to_the_trash(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    store = ProjectStore(tmp_path / "p")
    monkeypatch.setattr(appmod, "store", store)
    c = TestClient(appmod.app)
    pid = c.post("/api/demo").json()["id"]
    assert c.delete(f"/api/projects/{pid}").json()["trashed"] == pid
    assert pid not in [p["id"] for p in c.get("/api/projects").json()]
    assert (tmp_path / "p" / ".trash" / pid / "project.json").exists()     # recoverable
    assert c.delete(f"/api/projects/{pid}").status_code == 404
    assert "data_handover" in c.get("/api/health").json()["features"]


def test_team_shows_data_on_the_board_not_in_the_chat(office):
    o = office
    m = o.meetings.get(begin(o)["meeting_id"])
    n0 = len(m.slides)
    r = o.say("sampel 5", meeting_id=m.id)                        # $0 shortcut, before the join
    assert r["board"] and r["estimate"] is None and "papan" in r["text"]
    m = o.meetings.get(m.id)
    assert len(m.slides) == n0 + 1 and m.transcript[-1]["slide"] == n0   # on the projector
    page = (o.project.root / m.slides[-1]["file"]).read_text()
    assert page.count("<tr>") == 6                                # header + 5 rows
    o.choose(m.id, "acc")                                         # -> join + target meeting
    t = open_meeting(o)
    r = o.say("bad rate per cust_province", meeting_id=t.id)
    assert r["board"] and "bad_rate(bad)" in (o.project.root / r["board"][0]["file"]).read_text()
    # the tools: aggregates go back to the model, sample rows do not
    from autoscorecard.office.tools import ToolBox
    box = ToolBox(o, "bening")                                   # at a desk: the board window
    agg = json.loads(box.call("show_aggregate", {"group_by": ["cust_age"],
                                                 "metrics": [{"agg": "count"}, {"agg": "bad_rate"}]})[0])
    assert sum(r["n"] for r in agg["table"]) > 0 and len(agg["table"]) == 5   # 5 age bands
    smp = box.call("show_sample", {"n": 3, "columns": ["cust_age", "bad"]})[0]
    df, _ = o.dataset()
    assert "rows_shown" in smp and str(df["cust_age"].iloc[0]) not in json.loads(smp)["note"]
    assert set(json.loads(smp)) == {"shown_on_board", "rows_shown", "columns", "note"}
    assert [b["slide"] for b in o.board.list()[-2:]] == [None, None]    # desk items: no slide
    assert "tidak ada" in json.loads(json.dumps(box.call("show_sample", {"columns": ["nope"]})))[0]


def test_board_pages_are_served_but_raw_data_is_not(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    monkeypatch.setattr(appmod, "store", ProjectStore(tmp_path / "p"))
    appmod._OFFICES.clear()
    c = TestClient(appmod.app)
    pid = c.post("/api/demo").json()["id"]
    files = [f["name"] for f in c.get(f"/api/projects/{pid}/data").json()["files"]]
    c.post(f"/api/projects/{pid}/data/handover", json={"files": files})
    r = c.post(f"/api/projects/{pid}/office/say", json={"text": "sampel 3", "to": "bening"}).json()
    page = c.get(f"/api/projects/{pid}/files/{r['board'][0]['file']}")
    assert page.status_code == 200 and "Contoh 3 baris" in page.text
    assert c.get(f"/api/projects/{pid}/board").json()[0]["title"].startswith("Contoh 3")
    assert c.get(f"/api/projects/{pid}/files/raw/applications.csv").status_code == 404


def test_tables_written_in_a_reply_are_moved_to_the_board(tmp_path):
    o, _ = paid_office(tmp_path, [{"text": "<mood:neutral><ucap>Ini ringkasannya, Boss.\n"
                                           "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |</ucap>"}])
    r = o.say("@laras ringkas dong", to="laras", candid=True)
    assert "|" not in r["text"] and "papan" in r["text"] and len(r["board"]) == 1
    page = (o.project.root / r["board"][0]["file"]).read_text()
    assert "<td>3</td>" in page


def test_default_demo_data_is_labelled_and_can_be_removed(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    monkeypatch.setattr(appmod, "store", ProjectStore(tmp_path / "p"))
    appmod._OFFICES.clear()
    c = TestClient(appmod.app)
    pid = c.post("/api/demo").json()["id"]
    files = c.get(f"/api/projects/{pid}/data").json()["files"]
    assert files and all(f["default"] for f in files)
    gone = [f["name"] for f in files if f["name"] != "applications.csv"]
    assert c.post(f"/api/projects/{pid}/data/remove", json={"files": gone}).json()["removed"] == gone
    left = c.get(f"/api/projects/{pid}/data").json()["files"]
    assert [f["name"] for f in left] == ["applications.csv"]
    assert (tmp_path / "p" / pid / "work" / "removed" / "bureau.csv").exists()   # recoverable
    assert c.post(f"/api/projects/{pid}/data/remove", json={"files": ["nope.csv"]}).status_code == 404


def test_projector_slides_are_always_light():
    from autoscorecard.office.meetings import _page, light_only
    assert "prefers-color-scheme" not in _page("t", "<p>x</p>")
    svg = light_only("<svg><style>.t{fill:#000} @media (prefers-color-scheme: dark){.t{fill:#fff}}"
                     "</style></svg>")
    assert "dark" not in svg and ".t{fill:#000}" in svg


def test_the_office_shows_who_works_on_an_answer_and_each_step(tmp_path):
    o, _ = paid_office(tmp_path, [
        {"tools": [ToolCall("t1", "show_columns", {})]},
        {"text": "<mood:neutral><ucap>Daftar kolomnya sudah di papan, Boss.</ucap>"},
    ])
    for name, t in make_demo_data(n=300).items():
        t.to_csv(o.project.dir("raw") / f"{name}.csv", index=False)
    w = o.who_answers("sampel 5")
    assert w["agent"] == "bening" and w["line"]                  # instant, $0
    assert o.who_answers("@citra berapa gini?")["agent"] == "citra"
    o.say("@laras tunjukkan kolom datanya", to="laras", candid=True)
    steps = [e["data"]["task"] for e in o.events.since(0)
             if e["type"] == "agent_status" and e["actor"] == "laras" and e["data"]["status"] == "working"]
    assert "membaca daftar kolom" in steps                       # the real step, as it happened
    seq = {e["type"]: e["seq"] for e in o.events.since(0) if e["type"] in ("board", "dialogue")}
    assert seq["board"] < max(e["seq"] for e in o.events.since(0) if e["type"] == "dialogue")


def test_only_attendees_answer_and_the_whole_team_meets_at_kickoff(office):
    o = office
    m = o.meetings.get(begin(o)["meeting_id"])
    assert m.attendees == ["laras", "bening", "citra", "kirana"]          # Kirana too
    assert any(l["speaker"] == "kirana" for l in m.script)
    o.choose(m.id, "acc")
    o.choose(open_meeting(o).id, "acc")                                  # target ACC
    plan = open_meeting(o)
    assert plan.kind == "plan" and "bening" not in plan.attendees
    assert o.who_answers("cek join tabel data", plan.id)["agent"] in plan.attendees
    r = o.say("sampel 3", meeting_id=plan.id)                            # a Bening shortcut
    assert r["agent"] in plan.attendees and r["board"]


def test_the_boss_edits_the_waterfall_directly_and_it_shows(office):
    from autoscorecard.engine.waterfall import load_plan
    o = office
    o.choose(begin(o)["meeting_id"], "acc")
    o.choose(open_meeting(o).id, "acc")
    m = open_meeting(o)
    assert m.kind == "plan" and any(c["action"] == "edit_waterfall" for c in m.choices)
    steps = load_plan(o.project)["steps"]
    steps = [steps[1], steps[0]] + steps[2:]                      # drag: swap the first two
    for s in steps:
        if s["type"] == "missing_rate":
            s["params"]["max"] = 0.2
        if s["type"] == "forward":
            s["enabled"] = False
    r = o.edit_waterfall(steps)
    assert any("Missing rate: max 0.3 → 0.2" in c for c in r["changes"])
    assert any("Forward selection dimatikan" in c for c in r["changes"])
    assert any(c.startswith("urutan:") for c in r["changes"])
    m = o.meetings.get(m.id)
    assert "20%" in (o.project.root / m.slides[1]["file"]).read_text()     # projector rebuilt
    assert m.transcript[-1]["who"] == "citra" and "Missing rate" in m.transcript[-1]["text"]
    with pytest.raises(ValueError):                                # a bad plan says why
        o.edit_waterfall([{"type": "finalization"}, {"type": "iv"}])
    o.choose(m.id, "acc")                                          # Cut 1 uses the edit
    report = (o.project.dir("artifacts") / "cut1" / "waterfall_report.md").read_text()
    assert "20%" in report
    res = open_meeting(o)
    o.edit_waterfall([{**s, "params": {**s["params"], **({"min": 0.03} if s["type"] == "iv" else {})}}
                      for s in steps])                                # after Cut 1: next cut
    o.choose(res.id, "next")
    text = " ".join(l["text"] for l in open_meeting(o).script)
    assert "Rencana waterfall" in text and "Information Value" in text      # named in Cut 2


# --------------------------------------------------------------------------- timeline & teamwork

def test_timeline_edits(office):
    tl = office.timeline
    with pytest.raises(ValueError):                       # meetings decide these
        tl.update("target", "done")
    tl.update("dictionary", "done", "ditutup atas arahan Boss")
    assert tl.get("dictionary").status == "done"
    m = tl.add("Validasi independen", "laras", "cut2", before="deck", length=2)
    assert m.id == "x1" and tl.get("deck").depends_on == ["x1"]
    assert tl.slots()["deck"][0] > tl.slots()["x1"][1]
    with pytest.raises(ValueError):
        tl.remove("deck")
    tl.add_extension("Boss belum puas")                   # the added step stays in the plan
    assert set(tl.get("deck").depends_on) == {"x1", "cut3"}
    tl.remove("x1")
    assert set(tl.get("deck").depends_on) == {"cut3", "cut2"}


def test_laras_changes_the_timeline_and_remembers_the_desk(tmp_path):
    o, prov = paid_office(tmp_path, [
        {"text": "<mood:neutral> <ucap>Mau ditutup yang mana, Boss? 1. Kamus data 2. Deck</ucap>"},
        {"tools": [ToolCall("t1", "update_milestone", {"id": "dictionary", "status": "done",
                                                        "note": "ditutup atas arahan Boss"})]},
        {"text": "<mood:happy> <ucap>Sudah saya tutup, Boss. Timeline baru ada di papan.</ucap>"},
    ])
    o.say("@laras tutup salah satu timeline")
    r = o.say("@laras 1")
    assert o.timeline.get("dictionary").status == "done"
    assert "Timeline proyek" in [b["title"] for b in r["board"]]
    sent = [str(m.content) for m in prov.requests[1].messages]
    assert any("1. Kamus data" in c for c in sent)          # she saw her own question


def test_relay_parsing(office):
    p = office.parse_relay
    s = p("kamus suruh bening kerjakan, kasih ke citra untuk dipasangkan ke fitur terpilih lalu berikan ke kirana")
    assert [x["to"] for x in s] == ["bening", "citra", "kirana"]
    s = p("kamus data, suruh bening kerjakan, lalu serahkan ke citra, kemudian dokumentasikan di "
          "deck kiranan khusus variabel terpilih")
    assert [x["to"] for x in s] == ["bening", "citra", "kirana"]
    assert p("tolong bening cek missing rate cust_income") == []
    s = p("suruh bening kerjakan dan align dengan citra supaya draft deck bisa dibuat kirana segera")
    assert [x["to"] for x in s] == ["bening", "citra", "kirana"]
    assert p("tolong cek missing rate dan bad rate cust_income") == []


def test_relay_runs_step_by_step(office):
    o = office
    r = o.say("kamus suruh bening kerjakan, kasih ke citra untuk dipasangkan ke fitur terpilih "
              "lalu berikan ke kirana")
    assert r["agent"] == "laras" and "Alur #" in r["text"]
    parent = next(x for x in o.orders.list() if x["kind"] == "alur")
    assert [c["action"] for c in parent["chain"]] == ["document_variables", "feature_meanings", "make_deck"]
    begin(o)
    for _ in range(4):                                    # kickoff, target, plan, Cut 1 -> Cut 2
        m = open_meeting(o)
        o.choose(m.id, next(c["id"] for c in m.choices if c["action"] in
                            ("kickoff_acc", "target_acc", "plan_acc", "next_cut", "acc")))
    o.run_open_orders()
    parent = o.orders.get(parent["id"])
    assert parent["status"] == "selesai", parent
    kids = [o.orders.get(k) for k in parent["children"]]
    assert [k["to"] for k in kids] == ["bening", "citra", "kirana"]
    assert all(k["status"] == "selesai" for k in kids)
    assert (o.project.dir("artifacts") / "deliverables" / "fitur_terpilih.html").exists()
    subjects = [e.subject for e in o.inbox.list()]
    assert sum(s.startswith("Progress alur") for s in subjects) == 3


def test_laras_calls_a_meeting(tmp_path):
    o, prov = paid_office(tmp_path, [
        {"tools": [ToolCall("t1", "call_meeting", {"topic": "Bahas deck", "attendees": ["kirana"]})]},
        {"text": "<mood:happy> <ucap>Undangannya sudah terkirim, Boss.</ucap>"},
    ])
    o.say("@laras yuk meeting bahas deck dengan kirana")
    m = open_meeting(o)
    assert m.kind == "adhoc" and m.attendees == ["laras", "kirana"]
    o.choose(m.id, "done")
    assert o.state.open_meeting is None


def test_agents_ask_each_other(tmp_path):
    o, prov = paid_office(tmp_path, [
        {"tools": [ToolCall("t1", "ask_teammate", {"to": "citra", "question": "Gini OOT berapa?"})]},
        {"text": "<mood:neutral> <ucap>Belum ada cut yang dibangun, Laras.</ucap>"},
        {"text": "<mood:neutral> <ucap>Citra bilang belum ada cut, Boss.</ucap>"},
    ])
    r = o.say("@laras model kita bagus nggak?")
    assert r["text"] == "Citra bilang belum ada cut, Boss."
    names = {t.name for t in prov.requests[1].tools}
    assert "ask_teammate" not in names and "assign_task" not in names   # no loops
    assert "update_milestone" in {t.name for t in prov.requests[0].tools}


def test_checking_a_teammates_work(tmp_path):
    o, prov = paid_office(tmp_path, [
        {"tools": [ToolCall("t1", "check_teammate_work", {"to": "bening"})]},
        {"text": "<mood:neutral> <ucap>Bening masih menunggu data, Boss.</ucap>"},
    ])
    o.say("kamus suruh bening kerjakan, kasih ke citra untuk dipasangkan ke fitur terpilih "
          "lalu berikan ke kirana")
    parent = next(x for x in o.orders.list() if x["kind"] == "alur")
    n = len(prov.requests)
    # the Boss asks: answered from the orders, free
    r = o.say("@laras Bening lagi ngerjain apa?")
    assert r["agent"] == "laras" and "Bening:" in r["text"] and "terhambat" in r["text"]
    r = o.say(f"alur #{parent['id']} sampai mana?")
    assert f"#{parent['id']}" in r["text"] and "1. Bening" in r["text"] and "belum mulai" in r["text"]
    r = o.say("progres tim gimana?")
    assert all(f"{nm}:" in r["text"] for nm in ("Bening", "Citra", "Kirana"))
    assert len(prov.requests) == n
    w = o.team_work("bening")["Bening"]
    assert w["open"][0]["step_of_relay"] == parent["id"] and w["milestones"]
    assert o.team_work(order=parent["id"])["relay"][0].endswith("(sekarang)")
    # a teammate checks with the tool before answering
    r = o.say("@laras kamus dari bening sudah beres belum?")
    assert r["text"] == "Bening masih menunggu data, Boss."
    assert "check_teammate_work" in {t.name for t in prov.requests[n].tools}
    assert "Bening" in str(prov.requests[n + 1].messages[-1].content)

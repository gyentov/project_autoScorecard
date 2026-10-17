"""Terminal office (until the 2b pixel office exists).

    .venv/bin/python scripts/office_cli.py --demo        # new project with demo data
    .venv/bin/python scripts/office_cli.py --project ID  # continue a project

In a meeting: type a number to choose, or type anything to talk to the team
("@citra kenapa province dibuang?", "paksa cust_province", "bins cust_age 25 35 45").
Commands: /inbox  /read N  /timeline  /proposals  /acc ID  /tolak ID  /quit
"""
from __future__ import annotations

import argparse
import sys
import time

import pandas as pd

from autoscorecard.config import load_settings
from autoscorecard.demo_data import DEMO_DICTIONARY, make_demo_data
from autoscorecard.llm import LLMRouter
from autoscorecard.office import Office
from autoscorecard.office.characters import TEAM
from autoscorecard.office.timeline import STATUS_ICON, STATUS_LABEL
from autoscorecard.store import ProjectStore

C = {"laras": "\033[95m", "bening": "\033[96m", "citra": "\033[94m", "kirana": "\033[93m",
     "boss": "\033[92m", "dim": "\033[2m", "bold": "\033[1m", "end": "\033[0m"}


def speak(who: str, mood: str, text: str, slow: bool = True) -> None:
    name = "Boss" if who == "boss" else TEAM[who].name
    print(f"{C.get(who, '')}{C['bold']}{name}{C['end']} {C['dim']}({mood}){C['end']}  ", end="")
    for ch in text:
        print(ch, end="", flush=True)
        if slow:
            time.sleep(0.008)
    print()


def play(o: Office, mid: str) -> None:
    m = o.meetings.start(mid)
    print(f"\n{C['bold']}━━ {m.title} ━━{C['end']}")
    shown = None
    for ln in m.script:
        if ln["slide"] is not None and ln["slide"] != shown:
            s = m.slides[ln["slide"]]
            print(f"{C['dim']}  [proyektor] {s['title']}  → projects/{o.project.id}/{s['file']}"
                  f"{C['end']}")
            shown = ln["slide"]
        speak(ln["speaker"], ln["mood"], ln["text"])


def choices(o: Office, mid: str) -> list[dict]:
    m = o.meetings.get(mid)
    print()
    for k, c in enumerate(m.choices, 1):
        print(f"  {C['bold']}[{k}]{C['end']} {c['label']}")
    print(f"  {C['dim']}…atau ketik bebas{C['end']}")
    return m.choices


def wait_for_meeting(o: Office, last_seq: int) -> int:
    """Show build progress until a new meeting opens (or the job fails)."""
    while True:
        for ev in o.events.since(last_seq):
            last_seq = ev["seq"]
            d = ev["data"]
            if ev["type"] == "job_progress" and "n_out" in d.get("detail", {}):
                det = d["detail"]
                print(f"{C['dim']}  Citra: {d['step']}: {det['n_in']} → {det['n_out']} fitur"
                      f"{C['end']}")
            elif ev["type"] == "job_finished":
                print(f"{C['dim']}  Citra: build {'selesai' if d['ok'] else 'GAGAL: ' + d.get('error', '')}"
                      f"{C['end']}")
            elif ev["type"] == "email_sent":
                print(f"{C['dim']}  ✉ {TEAM[ev['actor']].name}: {d['subject']}{C['end']}")
        st = o.state
        if st.open_meeting and o.meetings.get(st.open_meeting).status == "scheduled":
            return last_seq
        if st.cuts.get(st.current_cut, {}).get("status") == "failed":
            return last_seq
        time.sleep(0.3)


def command(o: Office, cmd: str) -> bool:
    parts = cmd.split()
    if parts[0] == "/quit":
        sys.exit(0)
    if parts[0] == "/inbox":
        for k, e in enumerate(o.inbox.list(), 1):
            print(f"  {k:>2}. {'  ' if e.read else '● '}{e.kind:<8} {e.subject}")
    elif parts[0] == "/read" and len(parts) > 1:
        e = o.inbox.list()[int(parts[1]) - 1]
        o.inbox.mark_read(e.id)
        print(f"\n{C['bold']}{e.subject}{C['end']}  (dari {TEAM[e.sender].name})\n{e.body_md}")
        if e.attachments:
            print(f"{C['dim']}Lampiran: " + ", ".join(e.attachments) + C["end"])
    elif parts[0] == "/timeline":
        for m in o.timeline.items:
            print(f"  {STATUS_ICON[m.status]} {m.title:<24} {TEAM[m.owner].name:<7} "
                  f"{STATUS_LABEL[m.status]}{'  · ' + m.note if m.note else ''}")
    elif parts[0] == "/proposals":
        for p in o.proposals.list("pending"):
            print(f"  {p.id}  {p.summary}")
    elif parts[0] in ("/acc", "/tolak") and len(parts) > 1:
        print(o.decide_proposal(parts[1], parts[0] == "/acc"))
    else:
        return False
    return True


def talk(o: Office, text: str, mid: str | None) -> None:
    r = o.say(text, meeting_id=mid)
    speak(r["agent"], r["mood"], r["text"], slow=False)
    if r["needs_cost_ok"] and input("  Lanjut? (y/n) ").strip().lower().startswith("y"):
        r = o.say(text, meeting_id=mid, cost_ok=True)
        speak(r["agent"], r["mood"], r["text"], slow=False)
    if r.get("actual") and r["actual"]["actual"] > 0:
        print(f"{C['dim']}  Laras: biaya ${r['actual']['actual']:.4f} "
              f"(perkiraan ${r['estimate']['low_usd']:.4f}–${r['estimate']['high_usd']:.4f})"
              f"{C['end']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--project")
    args = ap.parse_args()
    settings = load_settings()
    store = ProjectStore(settings.app.projects_dir)
    if args.demo:
        proj = store.create("Demo - kredit sintetis")
        for name, t in make_demo_data().items():
            t.to_csv(proj.dir("raw") / f"{name}.csv", index=False)
        pd.DataFrame(DEMO_DICTIONARY).to_csv(proj.dir("raw") / "data_dictionary.csv",
                                             index=False)
        print(f"Project baru: {proj.id}")
    elif args.project:
        proj = store.get(args.project)
    else:
        ap.error("pakai --demo atau --project ID")
    o = Office(proj, LLMRouter(settings), settings)
    for ln in o.briefing():
        speak(ln["speaker"], ln["mood"], ln["text"])
    if o.state.stage == "new":
        o.start()
    last = 0
    while True:
        st = o.state
        mid = st.open_meeting
        if mid is None and st.stage == "cut" and \
                st.cuts.get(st.current_cut, {}).get("status") == "building":
            last = wait_for_meeting(o, last)
            continue
        if mid and o.meetings.get(mid).status == "scheduled":
            play(o, mid)
        opts = choices(o, mid) if mid else []
        text = input(f"\n{C['boss']}Boss>{C['end']} ").strip()
        if not text:
            continue
        if text.startswith("/"):
            if not command(o, text):
                print("  perintah: /inbox /read N /timeline /proposals /acc ID /tolak ID /quit")
            continue
        if text.isdigit() and mid and 1 <= int(text) <= len(opts):
            seen = o.events.since(0)
            last = seen[-1]["seq"] if seen else 0      # show progress from here on
            res = o.choose(mid, opts[int(text) - 1]["id"])
            if res.get("note"):
                print(f"  {res['note']}")
            continue
        talk(o, text, mid)


if __name__ == "__main__":
    main()

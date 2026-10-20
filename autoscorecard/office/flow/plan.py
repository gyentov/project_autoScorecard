"""Citra's waterfall plan meeting and the plan editor."""
from __future__ import annotations

import json

import pandas as pd

from ...engine.waterfall import approve_plan, load_plan, parse_steps, save_plan
from ...engine.waterfall_report import plan_svg
from .. import edareview as ER
from ..meetings import _page, choice, html_table, light_only, say
from .common import adjourn_choice, plan_diff


class PlanMixin:
    def plan_stage(self) -> dict:
        self._update(stage="plan")
        self.timeline.set("waterfall_plan", "in_progress")
        self._status("citra", "working", "menyusun rencana waterfall")
        plan = load_plan(self.project, self.settings.modelling.waterfall)
        steps = parse_steps(plan["steps"])
        save_plan(self.project, steps, by="citra")
        script = [
            say("citra", "neutral", "Boss, ini urutan seleksi fitur yang saya usulkan.", 0),
            *([say("citra", "thinking", "Arti tiap variabel sudah didokumentasikan Bening dan "
                   "sudah saya baca; kamusnya ada di slide terakhir.", 2)]
              if (self.project.dir("work") / "variable_meanings.json").exists() else []),
            say("citra", "thinking", "Filter data dulu (missing, identik, IV), lalu klaster "
                "korelasi, lalu stepwise, forward, backward, dan finalisasi.", 1),
            say("citra", "serious", "Model tidak saya bangun sebelum rencana ini di-ACC.", 1),
        ]
        slides = self._plan_slides(steps)
        eda = next((k for k, sl in enumerate(slides) if sl[0] == "EDA Bening & diskusi"), None)
        doc = ER.working(self.project)
        if eda is not None and doc:
            n_dis = len(doc["discussions"])
            script.insert(2, say("citra", "thinking", f"EDA Bening juga sudah saya baca: "
                                 f"{len(doc['points'])} poin, {n_dis} saya tidak setuju dan sudah kami "
                                 "diskusikan; notulennya sudah dicek Laras. Hasilnya di slide ini.", eda))
        props = [p for p in self.proposals.list("pending") if p.id in self._eda_proposal_ids(doc or
                                                                                         {"review": {}, "discussions": {}})]
        self.timeline.set("waterfall_plan", "waiting_boss")
        self._status("citra", "waiting_boss", "rapat rencana waterfall")
        mt = self.meetings.create(
            "plan", "Rapat rencana waterfall", "citra", ["laras", "citra"],
            slides, script,
            [*[c for p in props for c in (
                choice(f"acc_{p.id}", f"ACC (EDA): {p.summary}", "proposal", pid=p.id, accept=True),
                choice(f"rej_{p.id}", f"Tolak (EDA): {p.summary}", "proposal", pid=p.id, accept=False))],
             choice("acc", "ACC rencana, bangun Cut 1", "plan_acc"),
             choice("edit", "✏ Edit rencana waterfall sendiri (drag & drop)", "edit_waterfall"),
             adjourn_choice()],
            context="Rapat rencana waterfall: " + "; ".join(
                f"{s.title}: {s.criterion}" for s in steps if s.enabled),
            invite_body="Citra menjelaskan langkah seleksi fitur sebelum membangun model.")
        self._update(open_meeting=mt.id)
        return {"meeting_id": mt.id}

    def _plan_slides(self, steps) -> list[tuple[str, str, str]]:
        rows = [{"#": k + 1, "langkah": s.title, "fitur lolos jika": s.criterion}
                for k, s in enumerate([s for s in steps if s.enabled])]
        slides = [("Rencana", plan_svg(steps), "svg"), ("Kriteria", html_table(rows), "html")]
        p = self.project.dir("work") / "variable_meanings.json"
        if p.exists():                            # what Bening handed over, for reference
            vm = json.loads(p.read_text(encoding="utf-8"))
            slides.append(("Kamus variabel (Bening)", html_table(
                [{k: r[k] for k in ("variabel", "arti", "satuan", "dasar", "keyakinan")} for r in vm]),
                "html"))
        if (eda := self._eda_slide()) is not None:
            slides.append(eda)
        return slides

    def _refresh_plan_slides(self, m) -> None:
        """Rewrite the plan meeting's slides from the current plan (the projector reloads)."""
        steps = parse_steps(load_plan(self.project, self.settings.modelling.waterfall)["steps"])
        for k, (title, content, ext) in enumerate(self._plan_slides(steps)[:len(m.slides)]):
            f = self.project.root / m.slides[k]["file"]
            f.write_text(light_only(content) if ext == "svg" else _page(title, content),
                         encoding="utf-8")

    def edit_waterfall(self, steps: list[dict]) -> dict:
        """The Boss edits the waterfall directly (order, on/off, parameters): it is the
        Boss's decision, so it applies at once (no ACC). Citra confirms what changed; in
        the plan meeting the slides on the projector are rebuilt; after Cut 1 it applies
        to the next cut. Bad plans raise ValueError with the reason."""
        old = parse_steps(load_plan(self.project, self.settings.modelling.waterfall)["steps"])
        new = parse_steps(steps)
        changes = plan_diff(old, new)
        if not changes:
            return {"changes": []}
        save_plan(self.project, new, approved=True, by="boss")
        text = "; ".join(changes)
        st = self.state
        st.plan_edits.append({"ts": pd.Timestamp.now(tz="UTC").isoformat(timespec="microseconds"),
                              "summary": f"Rencana waterfall: {text}"})
        st.save(self.project)
        self.project.audit("boss", "waterfall_edit", {"changes": changes})
        self.events.emit("waterfall_edited", "boss", changes=changes)
        m = self.meetings.get(st.open_meeting) if st.open_meeting else None
        if m is not None and m.status != "ended" and m.kind == "plan":
            self._refresh_plan_slides(m)
            self.meetings.add_turn(m, "citra", f"Siap, Boss. Rencana sudah saya perbarui: {text}. "
                                   "Slide di proyektor sudah diganti.", "happy", slide=0)
        elif m is not None and m.status != "ended":
            self.meetings.add_turn(m, "citra", f"Dicatat, Boss: {text}. Berlaku mulai cut "
                                   "berikutnya.", "happy")
        else:
            self.events.emit("dialogue", "citra", mood="happy", text=f"Rencana waterfall "
                             f"diperbarui, Boss: {text}." + (" Berlaku mulai cut berikutnya."
                                                              if st.cuts else ""))
        return {"changes": changes}

    def _do_edit_waterfall(self, m) -> dict:      # the page opens the editor for this choice
        return {"note": "buka editor waterfall"}

    def _do_plan_acc(self, m) -> dict:
        approve_plan(self.project, by="boss")
        self.timeline.set("waterfall_plan", "done")
        self._end(m, "Rencana waterfall di-ACC.", ["Citra: membangun Cut 1."])
        self.events.emit("approval", "boss", what="waterfall_plan", decision="accepted")
        return self.build_cut("cut1")

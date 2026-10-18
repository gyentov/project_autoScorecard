"""The Boss's actions, the data handover with Laras, and messages passed on."""
from __future__ import annotations

import json

from ...engine import data as D
from .. import prep
from ..characters import TEAM, line
from ..meetings import choice, html_table, say
from ..orders import is_order
from ..summary import summarize
from ..timeline import gantt_svg
from .common import adjourn_choice


class HandoverMixin:
    def start(self) -> dict:
        """Kickoff: Laras introduces the team and the timeline."""
        st = self.state
        if st.stage != "new":
            return {"meeting_id": st.open_meeting, "note": "sudah dimulai"}
        tables, _ = prep.split_uploads(self.project)
        if st.data_files is None or not tables:          # Laras confirms the data first
            files = [p.name for p in self.project.raw_files()]
            note = (f"Boss, di proyek ini sudah ada {len(files)} file data. Sebelum kickoff, "
                    "tolong konfirmasi dulu file mana yang dipakai." if files else
                    "Boss, datanya belum ada. Upload dulu ya, baru kita mulai kickoff.")
            return {"needs_data": True, "note": note}
        return self._kickoff_meeting()

    def _kickoff_meeting(self) -> dict:
        self.timeline.set("kickoff", "in_progress")
        self._status("laras", "working", "menyiapkan kickoff")
        tables, _ = prep.split_uploads(self.project)
        files = ", ".join(tables) if tables else "belum ada"
        team = html_table([{"nama": c.name, "peran": c.role} for c in TEAM.values()])
        policy = ("<ul><li>Semua hitungan oleh engine; kami menjelaskan dan mengusulkan.</li>"
                  "<li>Setiap perubahan model butuh ACC Boss.</li>"
                  "<li>Sebelum pekerjaan berbayar, saya beri perkiraan biaya (rentang); "
                  "di atas batas, saya tanya dulu.</li>"
                  "<li>Cut 1 lalu Cut 2 (final); bisa diperpanjang kalau Boss belum puas.</li></ul>")
        script = [
            say("laras", "happy", f"{line('greet')} Saya Laras, project lead. Ini tim kita.", 0),
            say("bening", "happy", "Saya Bening, urusan data, join, dan target.", 0),
            say("citra", "neutral", "Saya Citra, modeller. Saya yang membangun scorecard.", 0),
            say("kirana", "excited", "Saya Kirana, pembuat presentasi. Hasil akhirnya nanti "
                "saya susun jadi deck yang enak dibaca, Boss.", 0),
            say("laras", "neutral", "Ini urutan pekerjaan kita. Tidak pakai tanggal, hanya "
                "urutan; yang menunggu Boss akan saya tandai.", 1),
            say("laras", "serious", "Aturan kerja kami ada di slide ini, termasuk soal biaya.", 2),
            say("laras", "neutral", f"Data yang sudah diupload: {files}. Kalau timeline-nya "
                "oke, Bening langsung mulai.", 2),
        ]
        m = self.meetings.create(
            "kickoff", "Kickoff proyek", "laras", ["laras", "bening", "citra", "kirana"],
            [("Tim", team, "html"), ("Timeline", gantt_svg(self.timeline), "svg"),
             ("Cara kerja", policy, "html")],
            script, [choice("acc", "ACC timeline, mulai", "kickoff_acc"), adjourn_choice()],
            context="Rapat kickoff. Jelaskan timeline, tim, dan aturan biaya.",
            invite_body="Kita sepakati timeline dan cara kerja tim.")
        self._update(stage="kickoff", open_meeting=m.id)
        self.timeline.set("kickoff", "waiting_boss")
        self._status("laras", "waiting_boss", "kickoff")
        return {"meeting_id": m.id}

    def choose(self, meeting_id: str, choice_id: str) -> dict:
        m = self.meetings.get(meeting_id)
        if m.status == "ended":
            raise ValueError("rapat sudah selesai")
        c = next((c for c in m.choices if c["id"] == choice_id), None)
        if c is None:
            raise KeyError(choice_id)
        self.meetings.start(meeting_id)
        m = self.meetings.get(meeting_id)
        self.events.emit("meeting_choice", "boss", meeting_id=meeting_id, choice=c["label"])
        self.project.audit("boss", "meeting_choice", {"meeting": m.title, "choice": c["label"]})
        handler = getattr(self, "_do_" + c["action"])
        return handler(m, **c["payload"]) or {}

    # ================================================================= data handover
    DATA_OPEN = ("new", "kickoff")          # after this the data feeds the model: locked

    def _data_open(self) -> bool:            # also open while Bening still waits for data
        st = self.state
        return st.stage in self.DATA_OPEN or (st.stage == "data" and st.join_plan is None)

    def default_files(self) -> set[str]:
        """Files that came with the demo (synthetic), not from the Boss."""
        p = self.project.dir("work") / "default_files.json"
        if p.exists():
            return set(json.loads(p.read_text(encoding="utf-8")))
        if self.project.meta.name.startswith("Demo"):       # demo projects made before the list
            from ...demo_data import make_demo_data
            return {f"{n}.csv" for n in make_demo_data(n=10)} | {"data_dictionary.csv"}
        return set()

    def remove_data(self, files: list[str]) -> dict:
        """Take files out of the project before the data stage: moved to work/removed/
        (recoverable), and dropped from the Boss's selection."""
        if not self._data_open():
            raise ValueError("data sudah dipakai sejak tahap data; tidak bisa dihapus di proyek ini")
        names = {p.name: p for p in self.project.raw_files()}
        missing = [f for f in files if f not in names]
        if missing:
            raise KeyError(", ".join(missing))
        dest = self.project.dir("work") / "removed"
        dest.mkdir(exist_ok=True)
        for f in files:
            names[f].replace(dest / f)
        st = self.state
        if st.data_files is not None:
            self._update(data_files=[f for f in st.data_files if f not in files] or None)
        self.project.audit("boss", "data_removed", {"files": files, "to": "work/removed"})
        return {"removed": files}

    def data_files(self) -> dict:
        """The raw/ files with their shape (counts only, never rows) and whether chosen."""
        chosen = self.state.data_files
        defaults = self.default_files()
        cache_p = self.project.dir("work") / "data_shapes.json"     # read each file once
        cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
        out = []
        for p in self.project.raw_files():
            st_ = p.stat()
            item = {"name": p.name, "bytes": st_.st_size,
                    "kind": "kamus" if prep.is_dictionary(p) else "tabel",
                    "selected": chosen is None or p.name in chosen,
                    "default": p.name in defaults}
            key = f"{p.name}|{st_.st_size}|{int(st_.st_mtime)}"
            if key not in cache:
                try:
                    t = D.load_table(p)
                    cache[key] = {"rows": len(t), "cols": len(t.columns)}
                except Exception as e:                   # unreadable: shown, not usable
                    cache[key] = {"rows": None, "cols": None, "error": str(e)[:200]}
            item.update(cache[key])
            out.append(item)
        cache_p.write_text(json.dumps(cache), encoding="utf-8")
        return {"files": out, "locked": not self._data_open(),
                "confirmed": self.state.data_files is not None,
                "stage": self.state.stage}

    def hand_over_data(self, files: list[str]) -> dict:
        """The Boss gives Laras the files to use. Laras thanks the Boss, says what she
        got, and passes it to Bening ($0)."""
        if not self._data_open():
            raise ValueError("data sudah dipakai sejak tahap data; untuk data lain, "
                             "buat proyek baru")
        names = {p.name for p in self.project.raw_files()}
        unknown = [f for f in files if f not in names]
        if unknown:
            raise KeyError(", ".join(unknown))
        if not files:
            raise ValueError("pilih minimal satu file")
        self._update(data_files=sorted(files))
        tables = [f for f in files if not prep.is_dictionary(self.project.dir("raw") / f)]
        dicts = [f for f in files if f not in tables]
        self.project.audit("boss", "data_handover", {"files": sorted(files)})
        self.events.emit("data_received", "laras", files=sorted(files))
        text = (f"Terima kasih, Boss. Saya terima {len(tables)} tabel"
                + (f" ({', '.join(tables)})" if tables else "")
                + (f" dan {len(dicts)} kamus data" if dicts else ", tanpa kamus data")
                + ". Saya teruskan ke Bening.")
        if self.state.stage == "new":
            text += " Kalau sudah siap, kita mulai kickoff."
        self.events.emit("dialogue", "laras", mood="happy", text=text)
        self.events.emit("handoff", "laras", to="bening", memo="data dari Boss")
        self.conversations.add(where="desk", boss="(menyerahkan data: " + ", ".join(sorted(files))
                               + ")", to="laras", agent="laras", text=text, mood="happy",
                               usd=0.0, estimate_high_usd=None, provider="", kind="handover",
                               thought="")
        if self.state.stage == "data":           # Bening was waiting for exactly this
            self._work("bening", ["membaca data dari Boss", "mencoba kunci join",
                                  "menggabungkan tabel"], self.data_stage)
        return {"files": sorted(files), "text": text}

    # ================================================================= messages ("titip pesan")
    def relay_message(self, via: str, to: str, text: str, kind: str = "pesan") -> dict:
        """A note from the Boss left with `via` for `to` (or a task Laras hands out after a
        meeting): `via` walks it over, `to` confirms she got it, the Boss gets a memo, and
        the note stays in `to`'s working context for her next answers."""
        gist = summarize(self, [text], literal=True)
        msg = self.orders.add(text, to, via=via, kind=kind, summary=gist)
        self.project.audit("boss" if kind == "pesan" else via, f"{kind}_relayed", msg)
        if via != to:
            self.events.emit("handoff", via, to=to, memo=f"{kind} dari Boss")
        what = "pesan Boss" if kind == "pesan" else "tugas dari rapat"
        ack = (f"Siap, {what} sudah saya terima" + (f" dari {TEAM[via].name}" if via != to else "")
               + f": {gist[0].lower()}{gist[1:]} Saya kerjakan.")
        self.events.emit("dialogue", to, mood="happy", text=ack)
        self.inbox.send(to, f"{what.capitalize()} diterima", f"Boss,\n\n{ack}\n\n{TEAM[to].name}",
                        "memo")
        if kind == "tugas" or is_order(text):           # a task is worked on, not just filed
            self._dispatch(msg)
        return msg

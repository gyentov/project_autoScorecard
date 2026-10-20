"""Bening's EDA: descriptive statistics, her points, Citra's review, their discussion and
Laras's check of the minutes."""
from __future__ import annotations

from ...engine import data as D, eda as E
from ...engine.waterfall import load_plan
from .. import edadoc as ED, edareview as ER, prep
from ..characters import TEAM


class EdaMixin:
    def run_eda(self) -> dict:
        """Bening describes the candidate features (engine/eda.py) and raises her points;
        Citra reviews each one; the points she disagrees with are discussed; Laras checks
        the minutes before they count as documentation. All rule-based, $0."""
        st = self.state
        t = st.target
        if not t:
            raise ValueError("EDA butuh target yang sudah disepakati (setelah rapat target)")
        df, y = self.dataset()
        self._status("bening", "working", "menghitung statistik deskriptif dan EDA")
        feats, _ = prep.feature_list(df, t, st.exclude)
        sp = D.split(df, y, date_col=t.get("date_col"), oot_from=t.get("oot_from"))
        dev = sp["train"].union(sp["test"])
        late = sp.get("oot")
        eda = E.describe(df, y, feats, dev_index=dev, late_index=late if late is not None and len(late) else None,
                         date_col=t.get("date_col"))
        doc = {"points": E.points(eda), "review": {}, "discussions": {}}
        self._status("bening", "idle")
        self.events.emit("handoff", "bening", to="citra", memo=f"EDA: {len(doc['points'])} poin untuk direview")
        why = "EDA pertama" if not ER.working(self.project) else "EDA diulang"
        rel = self._eda_file(eda, doc, {"status": "belum direview"})
        self._eda_mail_bening(eda, doc, rel)
        self._eda_citra(eda, doc)
        self._eda_mail_citra(doc, rel)
        review = self._eda_version(eda, doc, why)
        self._eda_file(eda, doc, review)                 # the attachment now carries the minutes
        self._eda_milestone("waiting_boss" if review["notes"] else "done",
                            f"{len(review['notes'])} catatan untuk Boss" if review["notes"] else "")
        return {"points": len(doc["points"]), "disagreed": len(doc["discussions"]),
                "review": review["status"], "file": rel,
                "proposals": self._eda_proposal_ids(doc)}

    # ------------------------------------------------------------- Citra reads, they discuss
    def _eda_citra(self, eda: dict, doc: dict) -> None:
        self._status("citra", "working", "membaca EDA Bening")
        plan = {s["type"]: s.get("params", {}) for s in
                load_plan(self.project, self.settings.modelling.waterfall)["steps"] if s.get("enabled", True)}
        treatment = (self.settings.modelling.outliers or {}).get("treatment", "none")
        for p in doc["points"]:
            rv = ER.citra_review(p, eda, plan, treatment)
            doc["review"][p["id"]] = rv
            spec = ER.agreed_proposal(p, rv)
            if spec:
                rv["proposal_id"] = self._eda_propose(spec, f"EDA {p['id']}: {p['finding']}")
        self._status("citra", "idle")
        for p in doc["points"]:
            if doc["review"][p["id"]]["stance"] == "tidak setuju":
                self._eda_discuss(doc, ER.discuss(p, doc["review"][p["id"]]))

    def _eda_discuss(self, doc: dict, d: dict) -> None:
        self._status("bening", "working", f"diskusi {d['point']} dengan Citra")
        self.events.emit("handoff", "citra", to="bening", memo=f"diskusi EDA {d['point']}")
        r = d["resolution"]
        if r.get("proposal"):
            r["proposal_id"] = self._eda_propose(r["proposal"], f"EDA {d['point']}: {r['decision']}")
        doc["discussions"][d["point"]] = d
        self.project.audit("bening", "eda_discussion", {"point": d["point"], "outcome": r["outcome"],
                                                        "decision": r["decision"]})
        self._status("bening", "idle")

    def _eda_propose(self, spec: dict, reason: str) -> str:
        """A proposal for the Boss's ACC; one already made (or decided) is not made again."""
        same = next((p for p in self.proposals.list() if p.kind == spec["kind"] and
                     all(p.payload.get(k) == v for k, v in spec["payload"].items())), None)
        if same is not None:
            return same.id
        if spec["kind"] == "derive_variable":
            return self.propose_derived(spec["payload"], "bening", reason)["proposal_id"]
        return self.proposals.add(spec["kind"], spec["payload"], "bening", reason).id

    @staticmethod
    def _eda_proposal_ids(doc: dict) -> list[str]:
        ids = [r.get("proposal_id") for r in doc["review"].values()]
        ids += [d["resolution"].get("proposal_id") for d in doc["discussions"].values()]
        return [i for i in dict.fromkeys(ids) if i]

    # ------------------------------------------------------------- Laras checks the minutes
    def _eda_version(self, eda: dict | None, doc: dict, why: str) -> dict:
        self._status("laras", "working", "mengecek notulen diskusi EDA")
        review = {**ER.run(doc, self._eda_propose), "why": why}
        ER.save(self.project, eda, doc, review)
        self._status("laras", "idle")
        self.project.audit("laras", "eda_review", {k: review[k] for k in ("status", "version", "why")})
        found = [f for rd in review["rounds"] for f in rd["found"] if f["blocking"]]
        fixed = [x for rd in review["rounds"] for x in rd.get("fixed", [])]
        body = (f"Boss, notulen diskusi EDA Bening–Citra versi {review['version']} ({why}) sudah saya cek: "
                f"{len(doc['points'])} poin, {len(doc['discussions'])} didiskusikan.\n\n"
                + "".join(f"- {pid} ({', '.join(d['variables'])}): "
                          f"{ER.OUTCOMES[d['resolution']['outcome']]}. {d['resolution']['decision']}\n"
                          for pid, d in doc["discussions"].items())
                + (f"\nTemuan yang harus diperbaiki: {len(found)}\n" + "\n".join(
                    f"- {f['point']}: {f['problem']}" for f in found) + "\n" if found else "")
                + ("\nSudah diperbaiki Bening:\n" + "\n".join(f"- {x}" for x in fixed) + "\n" if fixed else "")
                + ("\nHasil: **lolos review**; versi ini yang dipakai di dokumentasi." if review["status"] == "approved"
                   else "\nHasil: **belum lolos**; dokumentasi tetap memakai versi terakhir yang lolos. Sisa:\n"
                   + "\n".join(f"- {f['point']}: {f['problem']}" for f in review["blocking"]))
                + ("\n\nMenunggu Boss:\n" + "\n".join(f"- {n['point']}: {n['problem']}" for n in review["notes"])
                   if review["notes"] else "") + "\n\nLaras")
        self.inbox.send("laras", f"Review notulen diskusi EDA: {'lolos' if review['status'] == 'approved' else 'perlu revisi'}",
                        body, "memo")
        return review

    # ------------------------------------------------------------- files and mails
    def _eda_file(self, eda: dict, doc: dict, review: dict) -> str:
        out = self.project.dir("artifacts") / "eda_bening.html"
        out.write_text(ED.report(eda, doc, review, self.project.meta.name), encoding="utf-8")
        return str(out.relative_to(self.project.root))

    def _eda_mail_bening(self, eda: dict, doc: dict, rel: str) -> None:
        ov = eda["overview"]
        asks = [p for p in doc["points"] if p["action"] != "note"]
        self.inbox.send("bening", "Laporan EDA", f"Boss, Citra,\n\nEDA {ov['features']} kandidat fitur dari "
                        f"{ov['rows_known']:,} baris (bad rate {ov['bad_rate']:.1%}) sudah saya susun: statistik "
                        "deskriptif, hubungan tiap variabel dengan target, stabilitas, dan korelasi.\n\n"
                        f"Poin saya ({len(doc['points'])}, {len(asks)} di antaranya minta tindakan):\n"
                        + "\n".join(f"- {p['id']}: {p['finding']} Usul: {p['recommendation']}" for p in asks[:25])
                        + "\n\nLaporan lengkap terlampir.\n\nBening", "memo", attachments=[rel])

    def _eda_mail_citra(self, doc: dict, rel: str) -> None:
        dis = [p for p in doc["points"] if doc["review"][p["id"]]["stance"] == "tidak setuju"]
        self.inbox.send("citra", "Tanggapan EDA Bening", f"Bening, cc Boss,\n\nSaya sudah baca {len(doc['points'])} "
                        f"poin EDA: setuju {len(doc['points']) - len(dis)}, tidak setuju {len(dis)}."
                        + ("\n\nYang tidak saya setujui (sudah kita diskusikan):\n" + "\n".join(
                            f"- {p['id']}: {doc['review'][p['id']]['reason']}" for p in dis) if dis else "")
                        + "\n\nSemua poin jadi bahan saya menyusun rencana waterfall.\n\nCitra", "memo",
                        attachments=[rel])

    def _eda_milestone(self, status: str, note: str = "") -> None:
        try:
            self.timeline.set("eda", status, note)
        except KeyError:                          # projects made before the EDA milestone
            pass

    # ------------------------------------------------------------- later: a point raised, a lookup
    def dispute_eda(self, point: str, argument: str, by: str = "citra") -> dict:
        """Citra raises (or re-opens) a point with her argument; Bening answers from her
        evidence, and Laras checks the new version of the minutes."""
        doc = ER.working(self.project)
        if not doc:
            raise ValueError("EDA belum ada; minta Bening menjalankan EDA dulu")
        pid = point.strip().upper()
        p = next((x for x in doc["points"] if x["id"] == pid), None)
        if p is None:
            raise KeyError(f"poin {point} tidak ada (ada {doc['points'][0]['id']}–{doc['points'][-1]['id']})"
                           if doc["points"] else f"poin {point} tidak ada")
        if not argument.strip():
            raise ValueError("tulis argumennya, supaya Bening bisa menjawab")
        doc["review"][pid].update(stance="tidak setuju", reason=argument.strip(), by=by)
        self._eda_discuss(doc, ER.dispute_discussion(p, argument.strip()))
        review = self._eda_version(None, doc, f"poin {pid} diangkat {TEAM[by].name}")
        self._eda_file(ER.stats(self.project), doc, review)
        d = doc["discussions"][pid]["resolution"]
        return {"point": pid, "outcome": ER.OUTCOMES[d["outcome"]], "decision": d["decision"],
                "question_for_boss": d.get("question_for_boss"), "proposal": d.get("proposal_id"),
                "review": review["status"]}

    def eda_lookup(self, name: str | None = None) -> dict:
        """The EDA as documentation (the version Laras approved): a summary, one point
        (P3) or one variable's statistics, points and discussions."""
        doc = ER.approved(self.project)
        eda = ER.stats(self.project) or {}
        if not doc:
            return {"note": "belum ada EDA yang lolos review Laras"}
        head = {"review": doc["review_status"]["status"], "version": doc["review_status"]["version"]}
        if not name:
            return {**head, "overview": eda.get("overview"),
                    "points": [{k: r[k] for k in ("poin", "variabel", "usul Bening", "Citra", "hasil")}
                               for r in ED.points_rows(doc)][:40],
                    "correlated_pairs": eda.get("correlated_pairs", [])[:15]}
        if name.upper() in doc["review"]:
            pid = name.upper()
            return {**head, "point": next(p for p in doc["points"] if p["id"] == pid),
                    "citra": doc["review"][pid], "discussion": doc["discussions"].get(pid)}
        v = next((x for x in eda.get("variables", []) if x["variable"] == name), None)
        if v is None:
            raise KeyError(f"'{name}' bukan kandidat fitur di EDA")
        pts = [p for p in doc["points"] if name in p["variables"]]
        return {**head, "variable": v, "points": pts,
                "citra": {p["id"]: doc["review"][p["id"]] for p in pts},
                "discussions": {p["id"]: doc["discussions"][p["id"]] for p in pts if p["id"] in doc["discussions"]}}

    def _eda_slide(self) -> tuple[str, str, str] | None:
        doc, review = ER.working(self.project), ER.last_review(self.project)
        if not doc or not review:
            return None
        return ("EDA Bening & diskusi", ED.slide(doc, review), "html")

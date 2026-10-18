"""Bening's dictionary: its versions, Laras's review, meanings from discussions, derived variables."""
from __future__ import annotations

import json

from ...engine import data as D
from ...engine.dictionary import DataDictionary
from .. import derived as DV, dictreview as DR, meaning as MEAN, prep
from .common import _DATA, _now_iso


class DictionaryMixin:
    def document_variables(self, translate: bool = False) -> dict:
        """Bening's documented dictionary: what every variable and table means, where that
        comes from and how sure she is. Meanings settled in a discussion with the Boss and
        derived variables are kept when she works it out again. Laras reviews it (and Bening
        fixes what she finds) before it counts as documentation; then it is mailed to the
        Boss and handed to Citra with the dataset."""
        st = self.state
        t = st.target or {}
        try:
            df, _ = self.dataset()
        except FileNotFoundError:
            raise ValueError("menunggu data di-join (setelah kickoff)")
        ddict = DataDictionary.load(self.project)
        rows = MEAN.infer(df, ddict, office=self, plan=st.join_plan)
        translated = MEAN.translate(rows, self) if translate else 0
        tables_, dict_files = prep.split_uploads(self.project)
        table_rows = MEAN.tables(tables_, st.join_plan, [p.name for p in dict_files])
        rows, table_rows = self._keep_settled(rows, table_rows)
        for r in rows:                           # fill the gaps; the bank's own words stay
            e = ddict.entries.get(r["variabel"], {})
            if not e.get("description") and not r["dasar"].startswith("kamus data bank") and r["dasar"] != "-":
                ddict.set(r["variabel"], description=r["arti"] + (f" ({r['satuan']})" if r["satuan"] else ""))
                ddict.entries[r["variabel"]]["description_by"] = f"Bening ({r['dasar']})"
        ddict.save(self.project)
        review = self._dictionary_version(rows, table_rows, "kamus didokumentasikan")
        rel = self._dictionary_file(rows, table_rows, review)
        unclear = [r["variabel"] for r in rows if r["keyakinan"] == "rendah"]
        feats, _ = prep.feature_list(df, t, st.exclude) if t else (list(df.columns), {})
        rule = {"ge": ">=", "eq": "==", "in": "in"}.get(t.get("rule"), "")
        self.inbox.send("bening", "Kamus variabel sudah saya dokumentasikan",
                        f"Boss, sebelum ke Citra saya baca arti {len(rows)} variabel: "
                        f"{sum(r['dasar'] == 'kamus data bank' for r in rows)} dari kamus data bank, "
                        f"{sum(r['dasar'] == 'turunan join' for r in rows)} turunan join, "
                        f"{sum(str(r['dasar']).startswith('variabel turunan') for r in rows)} variabel "
                        "turunan, sisanya saya simpulkan dari nama dan rentang nilainya."
                        + (f"\n\nYang belum jelas (mohon konfirmasi): {', '.join(unclear)}."
                           if unclear else "")
                        + f"\n\nSudah direview Laras: {self._review_line(review)}"
                        + "\n\nKamus lengkap terlampir.\n\nBening", "memo", attachments=[rel])
        self.events.emit("handoff", "bening", to="citra",
                         memo=f"Dataset + kamus variabel: {len(df):,} baris, {len(feats)} kandidat fitur.")
        if t:
            self.inbox.send("bening", "Serah terima ke Citra", f"Citra, dataset siap: {len(df):,} "
                            f"baris, {len(feats)} kandidat fitur, target {t['column']} {rule} "
                            f"{t['value']}. Arti tiap variabel ada di kamus variabel terlampir."
                            "\n\ncc: Boss\n\nBening", "memo", attachments=[rel])
        self.timeline.set("dictionary", "waiting_boss" if unclear else "done",
                          f"{len(unclear)} variabel perlu konfirmasi" if unclear else "")
        return {"rows": rows, "tables": table_rows, "translated": translated, "unclear": unclear,
                "file": rel, "review": review["status"]}

    # ------------------------------------------------------------- the dictionary's versions
    def _keep_settled(self, rows: list[dict], tables: list[dict]) -> tuple[list[dict], list[dict]]:
        """What was settled stays when Bening works the dictionary out again: meanings from a
        discussion with the Boss, and derived variables (meaning = their formula)."""
        work = self.project.dir("work")
        old = {r["variabel"]: r for r in json.loads((work / "variable_meanings.json").read_text(
            encoding="utf-8"))} if (work / "variable_meanings.json").exists() else {}
        old_t = {r["tabel"]: r for r in json.loads((work / "table_meanings.json").read_text(
            encoding="utf-8"))} if (work / "table_meanings.json").exists() else {}
        means = {r["variabel"]: r["arti"] for r in rows}
        derived = {d["name"]: d for d in DV.load(self.project)}
        for r in rows:
            o = old.get(r["variabel"])
            if o and str(o.get("dasar", "")).startswith("diskusi"):
                r.update({k: o[k] for k in ("arti", "satuan", "dasar", "keyakinan", "riwayat") if k in o})
            elif r["variabel"] in derived:
                d = derived[r["variabel"]]
                r.update(arti=f"{DV.meaning(d, means)} (rumus: {d['formula']})",
                         dasar=f"variabel turunan: {d['formula']}", keyakinan="tinggi",
                         sumber="turunan Bening")
        for t in tables:
            o = old_t.get(t["tabel"])
            if o and str(o.get("dasar", "")).startswith("diskusi"):
                t.update({k: o[k] for k in ("arti", "dasar", "riwayat") if k in o})
        return rows, tables

    def _important_vars(self) -> set[str]:
        st = self.state
        out = set()
        for info in st.cuts.values():
            out.update(info.get("features", []))
        try:
            df, _ = self.dataset()
            if st.target:
                out.update(prep.feature_list(df, st.target, st.exclude)[0])
        except (FileNotFoundError, ValueError):
            pass
        return out

    def _dictionary_version(self, rows: list[dict], tables: list[dict], why: str) -> dict:
        """A new version of the dictionary: Laras reviews it, Bening fixes what she finds,
        Laras checks again; only an approved version becomes documentation."""
        self._status("laras", "working", "mereview kamus Bening")
        review = {**DR.run(self, rows, tables, self._important_vars()), "why": why}
        DR.save(self.project, rows, tables, review)
        self._status("laras", "idle")
        self.project.audit("laras", "dictionary_review", {k: review[k] for k in
                                                          ("status", "version", "why")})
        fixed = [x for rd in review["rounds"] for x in rd.get("fixed", [])]
        found = sum(len([f for f in rd["found"] if f["blocking"]]) for rd in review["rounds"])
        body = (f"Boss, kamus Bening versi {review['version']} ({why}) sudah saya review.\n\n"
                f"Temuan yang harus diperbaiki: {found}" + (":\n" + "\n".join(
                    f"- {f['item']}: {f['problem']}" for rd in review["rounds"] for f in rd["found"]
                    if f["blocking"]) if found else "") + "\n\n"
                + (f"Sudah diperbaiki Bening:\n" + "\n".join(f"- {x}" for x in fixed) + "\n\n" if fixed else "")
                + ("Hasil: **lolos review**; versi ini yang dipakai di dokumentasi dan deck." if
                   review["status"] == "approved" else
                   "Hasil: **belum lolos**; dokumentasi tetap memakai versi terakhir yang lolos. "
                   "Sisa masalah:\n" + "\n".join(f"- {f['item']}: {f['problem']}" for f in review["blocking"]))
                + ("\n\nCatatan untuk Boss:\n" + "\n".join(f"- {f['item']}: {f['problem']}"
                                                            for f in review["notes"][:20])
                   if review["notes"] else "") + "\n\nLaras")
        self.inbox.send("laras", f"Review kamus: {'lolos' if review['status'] == 'approved' else 'perlu revisi'}",
                        body, "memo")
        if review["status"] != "approved":
            self.timeline.set("dictionary", "in_progress", "kamus belum lolos review Laras")
        return review

    @staticmethod
    def _review_line(review: dict) -> str:
        fixed = sum(len(rd.get("fixed", [])) for rd in review["rounds"])
        return ("lolos" if review["status"] == "approved" else "belum lolos") + \
            (f", {fixed} perbaikan" if fixed else ", tanpa perbaikan") + \
            (f", {len(review['notes'])} catatan untuk Boss" if review["notes"] else "") + "."

    def _dictionary_file(self, rows: list[dict], tables: list[dict], review: dict) -> str:
        t = self.state.target or {}
        rule = {"ge": ">=", "eq": "==", "in": "in"}.get(t.get("rule"), "")
        md = MEAN.markdown(rows, f"bad = {t['column']} {rule} {t['value']}" if t else "")
        md = md.replace("| variabel |", MEAN.tables_markdown(tables) + "## Variabel\n\n| variabel |", 1)
        md = md.replace("\n\n", f"\n\nStatus review Laras: {self._review_line(review)} "
                        f"(versi {review['version']})\n\n", 1)
        out = self.project.dir("artifacts") / "kamus_variabel_bening.md"
        out.write_text(md, encoding="utf-8")
        return str(out.relative_to(self.project.root))

    def approved_meanings(self) -> list[dict]:
        """The dictionary as documentation: the last version Laras approved. A project that
        has a dictionary but was never reviewed gets its review now."""
        if not DR.last_review(self.project) and (self.project.dir("work") / "variable_meanings.json").exists():
            rows = json.loads((self.project.dir("work") / "variable_meanings.json").read_text(encoding="utf-8"))
            tp = self.project.dir("work") / "table_meanings.json"
            tables = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else []
            self._dictionary_version(rows, tables, "review pertama kamus yang sudah ada")
        return DR.approved_rows(self.project)

    def dictionary_lookup(self, name: str | None = None) -> dict:
        """The approved dictionary for any teammate: one variable / table, or a summary."""
        rows = self.approved_meanings()
        review = DR.last_review(self.project) or {}
        head = {"review": review.get("status", "belum direview"), "version": review.get("version")}
        if not rows:
            return {**head, "note": "belum ada kamus yang lolos review Laras"}
        if name:
            r = next((r for r in rows if r["variabel"] == name), None)
            if r is None:
                t = next((t for t in DR.approved_tables(self.project)
                          if t["tabel"] in (name, name.rsplit(".", 1)[0]) or t["tabel"].rsplit(".", 1)[0] == name), None)
                if t is None:
                    raise KeyError(f"'{name}' tidak ada di kamus")
                return {**head, "tabel": {k: v for k, v in t.items() if k != "riwayat"}}
            return {**head, "variabel": {k: v for k, v in r.items() if k not in ("statistik",)}}
        return {**head, "n_variabel": len(rows),
                "tabel": [{k: t[k] for k in ("tabel", "arti", "peran") if k in t}
                          for t in DR.approved_tables(self.project)],
                "perlu_konfirmasi": [r["variabel"] for r in rows if "konfirmasi" in str(r.get("arti"))][:30],
                "turunan": [r["variabel"] for r in rows if str(r.get("dasar", "")).startswith("variabel turunan")]}

    def update_meaning(self, name: str, arti: str, satuan: str | None = None,
                       reason: str = "", by: str = "bening") -> dict:
        """A meaning settled in a discussion (a variable or a table): Bening writes it down,
        with the old one kept in its history, and Laras reviews the new version."""
        work = self.project.dir("work")
        vp, tp = work / "variable_meanings.json", work / "table_meanings.json"
        rows = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else []
        tables = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else []
        if not rows:
            raise ValueError("kamus belum didokumentasikan; minta Bening mendokumentasikan dulu")
        target = next((r for r in rows if r["variabel"] == name), None)
        what = "variabel"
        if target is None:
            target = next((t for t in tables if t["tabel"] in (name, name.rsplit(".", 1)[0])
                           or t["tabel"].rsplit(".", 1)[0] == name), None)
            what = "tabel"
        if target is None:
            raise KeyError(f"'{name}' bukan variabel atau tabel di kamus")
        hist = target.setdefault("riwayat", [])
        hist.append({"arti": target.get("arti"), "dasar": target.get("dasar"), "ts": _now_iso()})
        target.update(arti=arti.strip(), dasar=f"diskusi dengan Boss ({_now_iso()[:10]})"
                      + (f": {reason}" if reason else ""))
        if what == "variabel":
            target["keyakinan"] = "tinggi"
            if satuan is not None:
                target["satuan"] = satuan
            ddict = DataDictionary.load(self.project)
            ddict.set(name, description=arti + (f" ({satuan})" if satuan else ""))
            ddict.entries[name]["description_by"] = "Bening (diskusi dengan Boss)"
            ddict.save(self.project)
        self.project.audit(by, "meaning_update", {"what": what, "name": name, "arti": arti,
                                                   "reason": reason})
        review = self._dictionary_version(rows, tables, f"arti {what} {name} diperbarui")
        self._dictionary_file(rows, tables, review)
        return {"updated": what, "name": name, "arti": arti, "review": review["status"],
                "review_notes": [f"{f['item']}: {f['problem']}" for f in review["blocking"] + review["notes"]][:8]}

    # ------------------------------------------------------------- derived variables (Bening)
    def derived_preview(self, spec: dict) -> dict:
        """What a proposed derived variable would bring, on the training sample only:
        missing rate, IV and its strength (aggregates, never rows)."""
        from ...engine.binning import bin_feature, iv_strength
        df, y = self.dataset()
        spec = DV.validate(spec, list(df.columns))
        x = DV.compute(df, spec)
        out = {"name": spec["name"], "formula": DV.formula(spec),
               "missing_rate": round(float(x.isna().mean()), 4)}
        if y is not None:
            t = self.state.target
            sp = D.split(df, y, date_col=t.get("date_col"), oot_from=t.get("oot_from"))
            tr = sp["train"]
            try:
                b = bin_feature(x.loc[tr], y.loc[tr].astype(int), name=spec["name"])
                out.update(iv=round(float(b.iv), 4), strength=iv_strength(b.iv), bins=len(b.table))
            except Exception as e:                      # e.g. a constant column
                out["iv_note"] = f"IV tidak bisa dihitung: {e}"
        return out

    def propose_derived(self, spec: dict, by: str = "bening", reason: str = "") -> dict:
        prev = self.derived_preview(spec)
        df, _ = self.dataset()
        spec = DV.validate(spec, list(df.columns))
        p = self.proposals.add("derive_variable", spec, by, reason or (
            f"pratinjau: IV {prev.get('iv', '-')} ({prev.get('strength', '-')}), "
            f"missing {prev['missing_rate']:.0%}"))
        return {"proposal_id": p.id, "summary": p.summary, "preview": prev,
                "status": "menunggu ACC Boss; masuk kandidat di cut berikutnya"}

    def _proposal_accepted(self, p) -> None:
        if p.kind == "derive_variable":
            _DATA.pop(self.project.id, None)             # the dataset now has the new column
            spec = p.payload
            try:
                self.update_dictionary_for_derived(spec)
            except (ValueError, KeyError) as e:
                self.inbox.send("bening", f"Variabel {spec['name']}: kamus belum diperbarui",
                                f"Boss, variabel {spec['name']} sudah ditambahkan, tapi kamusnya "
                                f"belum bisa saya perbarui: {e}.\n\nBening", "memo")

    def update_dictionary_for_derived(self, spec: dict) -> None:
        """A new derived variable joins Bening's dictionary (meaning = its formula); Laras
        reviews that version."""
        work = self.project.dir("work")
        vp, tp = work / "variable_meanings.json", work / "table_meanings.json"
        if not vp.exists():
            return                                    # documented with the first dictionary
        rows = json.loads(vp.read_text(encoding="utf-8"))
        tables = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else []
        means = {r["variabel"]: r["arti"] for r in rows}
        df, _ = self.dataset()
        rec = {"variabel": spec["name"], "sumber": "turunan Bening", "tipe": "angka",
               "statistik": MEAN._stats(df[spec["name"]]),
               "arti": f"{DV.meaning(spec, means)} (rumus: {DV.formula(spec)})", "satuan": "",
               "dasar": f"variabel turunan: {DV.formula(spec)}", "keyakinan": "tinggi"}
        rows = [r for r in rows if r["variabel"] != spec["name"]] + [rec]
        ddict = DataDictionary.load(self.project)
        ddict.set(spec["name"], description=rec["arti"])
        ddict.entries[spec["name"]]["description_by"] = "Bening (variabel turunan)"
        ddict.save(self.project)
        review = self._dictionary_version(rows, tables, f"variabel turunan {spec['name']} ditambahkan")
        rel = self._dictionary_file(rows, tables, review)
        self.inbox.send("bening", f"Variabel baru: {spec['name']}",
                        f"Boss, variabel {spec['name']} = {DV.formula(spec)} sudah masuk dataset dan "
                        f"kamus; jadi kandidat di cut berikutnya.\n\nReview Laras: "
                        f"{self._review_line(review)}\n\nBening", "memo", attachments=[rel])

    def _dictionary_memo(self) -> None:
        g = self.dictionary_gaps()
        if g["n_gaps"] == 0:
            self.timeline.set("dictionary", "done")
            return
        names = "\n".join(f"- {v['variable']}: {v['description']} / {v['variable_type']}"
                          for v in g["variables"][:40])
        self.inbox.send("bening", f"Kamus data: {g['n_gaps']} variabel belum lengkap",
                        "Boss, variabel berikut belum punya deskripsi atau tipenya masih "
                        f"tebakan:\n\n{names}\n\nBisa upload kamus data (kolom: variable, "
                        "description, category, logic) kapan saja; tidak menghambat "
                        "modelling, tapi dokumentasi jadi lengkap.\n\nBening", "memo")
        self.timeline.set("dictionary", "waiting_boss", f"{g['n_gaps']} variabel belum lengkap")

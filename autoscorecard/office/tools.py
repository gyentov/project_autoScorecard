"""Tools the agents can call. Read-only tools return aggregates (profiles, bin
tables, IV, metrics), never rows. Tools that change the model only create a
proposal; the Boss's ACC applies it.

The show_* tools put data ON THE BOARD for the Boss (the projector in a meeting, the
board window at a desk). show_aggregate and show_columns return their aggregates to
the model too; show_sample puts rows on the board and tells the model only how many
rows and which columns it showed.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from ..engine.binning import bin_feature, bin_numeric, is_numeric
from ..llm.types import ToolSpec
from . import board as B
from .characters import TEAM
from .meetings import html_table

MAX_CHARS = 4000
TEAM_NAME = lambda a: TEAM[a].name                                        # noqa: E731


def _json(obj: Any) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS] + '..."(truncated)"'


def _records(df: pd.DataFrame, n: int = 30) -> list[dict]:
    return df.head(n).round(4).to_dict("records")


class ToolBox:
    def __init__(self, office, agent: str, meeting_id: str | None = None, tasks: bool = True,
                 consult: bool = True):
        self.office, self.agent, self.meeting_id = office, agent, meeting_id
        self.created: list[str] = []          # proposal ids made during this turn
        self.shown: list[dict] = []           # board items put up during this turn
        self.tasks: list[dict] = []           # work handed to teammates (Laras after a meeting)
        self._tools: dict[str, tuple[ToolSpec, Callable[..., Any]]] = {}
        self._register()
        team = {"type": "string", "enum": ["laras", "bening", "citra", "kirana"]}
        if tasks:
            self._add("assign_task", "SERAHKAN PEKERJAAN ke rekan (Bening: data/target/kamus, "
                      "Citra: model/waterfall/binning/fitur, Kirana: deck/presentasi, Laras: "
                      "dokumentasi/timeline). Rekan langsung mengerjakannya dan melapor; tercatat "
                      "sebagai tugas.",
                      {"to": team, "task": {"type": "string", "description": "tugas yang jelas dan bisa dikerjakan"}},
                      ["to", "task"], self.assign_task)
            self._add("plan_relay", "Buat ALUR kerja beberapa rekan berurutan (misal Bening "
                      "mendokumentasikan kamus -> Citra memasangkan ke fitur terpilih -> Kirana "
                      "memasukkan ke deck). Tiap langkah mulai setelah langkah sebelumnya selesai, "
                      "dan Laras melapor ke Boss tiap langkah.",
                      {"steps": {"type": "array", "items": {"type": "object", "properties": {
                          "to": team, "task": {"type": "string"}}, "required": ["to", "task"]}}},
                      ["steps"], self.plan_relay)
        if consult:
            self._add("ask_teammate", "TANYA rekan (jawabannya kembali ke kamu, dengan data dari "
                      "tool rekan itu). Pakai saat pertanyaan Boss ada di bidang rekan.",
                      {"to": team, "question": {"type": "string"}}, ["to", "question"],
                      self.ask_teammate)
        if agent == "laras":
            self._timeline_tools()

    # ------------------------------------------------------------- plumbing
    def _add(self, name: str, desc: str, props: dict, required: list[str], fn) -> None:
        self._tools[name] = (ToolSpec(name, desc, {"type": "object", "properties": props,
                                                   "required": required}), fn)

    def specs(self) -> list[ToolSpec]:
        return [t[0] for t in self._tools.values()]

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        if name not in self._tools:
            return f"unknown tool {name}", True
        try:
            return _json(self._tools[name][1](**args)), False
        except (KeyError, ValueError, FileNotFoundError) as e:
            return f"error: {e}", True

    # ------------------------------------------------------------- tools
    def _register(self) -> None:
        o = self.office
        S = {"type": "string"}
        feat = {"feature": {**S, "description": "nama fitur persis"}}
        self._add("project_status", "Tahap proyek, timeline, usulan yang menunggu ACC, hasil cut "
                  "terakhir.", {}, [], o.status_summary)
        self._add("tables", "Tabel yang diupload: jumlah baris dan profil kolom.", {}, [],
                  o.tables_summary)
        self._add("join_plan", "Rencana join saat ini dan hasilnya.", {}, [],
                  lambda: o.state.join_plan or {})
        self._add("target_info", "Definisi target, bad rate total dan per periode, split OOT.",
                  {}, [], o.target_summary)
        self._add("feature_status", "Kenapa fitur terpilih/dibuang di waterfall cut terakhir "
                  "(IV, missing rate, langkah, alasan).", feat, ["feature"], o.feature_status)
        self._add("iv_table", "IV semua fitur di cut terakhir.", {}, [], o.iv_summary)
        self._add("bin_table", "Tabel bin (count, bad rate, WoE, poin) satu fitur di cut "
                  "terakhir.", feat, ["feature"], o.bin_summary)
        self._add("performance", "Gini/KS/AUC/PSI per sampel untuk tiap cut.", {}, [],
                  o.performance_summary)
        self._add("what_if_bins", "Coba bin manual TANPA mengubah model: hasil bad rate, WoE, "
                  "IV, monotonic.", {**feat, "edges": {"type": "array", "items": {
                      "type": "number"}}}, ["feature", "edges"], self.what_if_bins)
        self._add("dictionary_gaps", "Variabel tanpa deskripsi / tipe.", {}, [],
                  o.dictionary_gaps)
        self._add("check_teammate_work", "CEK STATUS PEKERJAAN rekan (atau semua tim): tugas "
                  "yang masih terbuka dengan catatan terbarunya, yang baru selesai, posisi alur, "
                  "dan tahap timeline miliknya. Pakai sebelum menjawab 'X lagi ngerjain apa' atau "
                  "sebelum menagih/menunggu hasil rekan; jangan menebak.",
                  {"to": {**S, "enum": ["laras", "bening", "citra", "kirana"],
                          "description": "rekan; kosong = semua tim"},
                   "order": {"type": "integer", "description": "nomor perintah/tugas/alur tertentu"}},
                  [], lambda to=None, order=None: o.team_work(to, order))
        self._add("cost_status", "Budget LLM terpakai dan sisa.", {}, [], o.cost_status)
        # showing data on the board (all agents)
        arr = lambda items: {"type": "array", "items": items}             # noqa: E731
        table = {"table": {**S, "description": "nama tabel upload, atau 'dataset' (gabungan, "
                           "dengan kolom bad bila target sudah ada). Kosong = dataset."}}
        filters = {"filters": arr({"type": "object", "properties": {
            "column": S, "op": {**S, "enum": ["==", "!=", ">", ">=", "<", "<=", "in"]},
            "value": {}}, "required": ["column", "value"]})}
        title = {"title": {**S, "description": "judul di papan"}}
        self._add("show_aggregate", "TAMPILKAN DI PAPAN agregasi data: group by kolom (numerik "
                  "otomatis dibagi kuantil), dengan metrik count, mean, sum, min, max, median, "
                  "nunique, missing_rate, bad_rate. Pakai ini setiap kali Boss minta melihat "
                  "angka rinci; jangan menulis tabel di ucapan.",
                  {"group_by": arr(S), "metrics": arr({"type": "object", "properties": {
                      "agg": {**S, "enum": list(B.METRICS)}, "column": S}, "required": ["agg"]}),
                   "bins": {"type": "integer"}, **table, **filters, **title},
                  ["group_by", "metrics"], self.show_aggregate)
        self._add("show_sample", "TAMPILKAN DI PAPAN contoh baris data (maks 20) untuk Boss. "
                  "Isi baris tidak dikirim ke kamu; kamu hanya diberi tahu jumlah baris dan "
                  "kolomnya.", {"n": {"type": "integer"}, "columns": arr(S), **table, **filters,
                                **title}, [], self.show_sample)
        self._add("show_vintage", "TAMPILKAN DI PAPAN vintage analysis: persentase akun yang "
                  "pernah X+ DPD per kohort booking menurut bulan sejak booking (MOB), kurva "
                  "30+/60+/90+, dan rekomendasi window serta ambang bad. Bahan menentukan target.",
                  {"threshold": {"type": "integer", "enum": [30, 60, 90]}}, [], self.show_vintage)
        self._add("show_waterfall", "TAMPILKAN DI PAPAN rencana waterfall saat ini: urutan "
                  "langkah, aktif/tidak, dan kriteria tiap langkah.", {}, [], self.show_waterfall)
        self._add("show_columns", "TAMPILKAN DI PAPAN daftar kolom sebuah tabel: tipe, missing "
                  "rate, jumlah nilai unik.", {**table}, [], self.show_columns)
        # the Boss's orders: report progress, and the work the office can do itself
        self._add("update_order", "Laporkan status perintah Boss: dikerjakan, selesai, atau "
                  "terhambat (dengan alasan).",
                  {"id": {"type": "integer"}, "status": {**S, "enum": ["dikerjakan", "selesai", "terhambat"]},
                   "note": S}, ["id", "status"],
                  lambda id, status, note="": o.orders.update(id, status, note))
        self._add("document_variables", "Dokumentasikan arti setiap variabel dan tabel, terjemahkan "
                  "kamus bank ke bahasa Indonesia; hasilnya file kamus dan email ke Boss.", {}, [],
                  lambda: {k: v for k, v in o.document_variables(translate=True).items() if k != "rows"})
        self._add("make_documentation", "Susun dokumentasi model lengkap (HTML) dari cut terakhir.",
                  {}, [], o.make_documentation)
        self._add("make_deck", "Susun draft deck presentasi (HTML, bahasa Inggris) untuk direview Boss.",
                  {}, [], o.make_deck)
        # proposals: never change anything by themselves
        reason = {"reason": {**S, "description": "alasan, untuk notulen"}}
        self._add("propose_force_feature", "USULKAN fitur dipaksa dicoba di model final "
                  "(butuh ACC).", {**feat, **reason}, ["feature"],
                  lambda feature, reason="": self._propose("force_feature",
                                                           {"feature": feature}, reason))
        self._add("propose_exclude_feature", "USULKAN fitur dibuang dari kandidat (butuh ACC).",
                  {**feat, **reason}, ["feature"],
                  lambda feature, reason="": self._propose("exclude_feature",
                                                           {"feature": feature}, reason))
        self._add("propose_bin_edit", "USULKAN titik potong bin manual (butuh ACC).",
                  {**feat, "edges": {"type": "array", "items": {"type": "number"}}, **reason},
                  ["feature", "edges"],
                  lambda feature, edges, reason="": self._propose(
                      "bin_edit", {"feature": feature, "edges": edges}, reason))
        self._add("propose_target", "USULKAN definisi target baru: bad = kolom rule nilai "
                  "(rule: ge, eq, in). Butuh ACC Boss.",
                  {"column": S, "rule": {**S, "enum": ["ge", "eq", "in"]}, "value": {}, **reason},
                  ["column", "rule", "value"],
                  lambda column, rule, value, reason="": self._propose(
                      "target", {"column": column, "rule": rule, "value": value}, reason))
        self._add("propose_plan_change", "USULKAN ubah parameter langkah waterfall (butuh ACC).",
                  {"step": S, "params": {"type": "object"}, **reason}, ["step", "params"],
                  lambda step, params, reason="": self._propose(
                      "plan_change", {"step": step, "params": params}, reason))

    # ------------------------------------------------------------- the board
    def _show(self, title: str, body: str) -> dict:
        item = self.office.board.show(title, body, self.agent, self.meeting_id)
        self.shown.append(item)
        return item

    def show_aggregate(self, group_by: list[str], metrics: list[dict], bins: int = 5,
                       table: str | None = None, filters: list[dict] | None = None,
                       title: str = "") -> dict:
        df, what = B.frame(self.office, table)
        df, fnote = B._filter(df, filters)
        t = B.aggregate(df, group_by, metrics, bins)
        vals = [c for c in t.columns if c not in group_by]
        main = vals[-1] if any(c.startswith("bad_rate") for c in vals) else vals[0]
        title = title or (", ".join(vals) + (" per " + ", ".join(group_by) if group_by else ""))
        rec = B.records(t)
        body = (f'<p class="muted">{what}, {len(df):,} baris' + (f"; filter: {fnote}" if fnote
                                                                else "") + "</p>"
                + (B.bars_svg(t, group_by, main, main.startswith(("bad_rate", "missing_rate")))
                   if group_by and len(t) > 1 else "") + html_table(rec))
        self._show(title, body)
        return {"shown_on_board": title, "source": what, "rows_used": len(df), "table": rec}

    def show_sample(self, n: int = 10, columns: list[str] | None = None,
                    table: str | None = None, filters: list[dict] | None = None,
                    title: str = "") -> dict:
        df, what = B.frame(self.office, table)
        df, fnote = B._filter(df, filters)
        smp = B.sample(df, n, columns)
        title = title or f"Contoh {len(smp)} baris: {what}"
        body = (f'<p class="muted">Acak dari {len(df):,} baris' + (f"; filter: {fnote}" if fnote
                                                                   else "") + "</p>"
                + html_table(B.records(smp.reset_index(drop=True))))
        self._show(title, body)
        # rows go to the Boss's board only; the model learns the shape, not the values
        return {"shown_on_board": title, "rows_shown": len(smp), "columns": list(smp.columns),
                "note": "isi baris hanya tampil di papan Boss"}

    def show_vintage(self, threshold: int | None = None) -> dict:
        from ..engine import vintage as V
        vin = self.office.vintage()
        if not vin:
            raise ValueError("vintage butuh tabel riwayat performa bulanan (kolom id akun, "
                             "mob atau tanggal snapshot, dan dpd), dibaca setelah join")
        rec, c = vin["rec"], vin["curves"]
        t = int(threshold or rec["threshold"])
        rows = [{"ambang": f"{r['threshold']}+", "MOB matang": r["mature_mob"],
                 "window": r["window"], "bad rate": round(r["bad_rate"], 4), "bad": r["bads"],
                 "akun matang": r["n_mature"]} for r in rec["by_threshold"]]
        self._show(f"Vintage {t}+ DPD", V.curves_svg(c, t, rec["window"], f"Pernah {t}+ DPD, per "
                   "kuartal booking") + V.curves_svg(c, None, rec["window"], "Semua kohort")
                   + html_table(rows))
        return {"shown_on_board": f"Vintage {t}+ DPD", "history_table": vin["spec"]["table"],
                "recommendation": {k: rec[k] for k in ("threshold", "window", "bad_rate",
                                                       "bads", "n_mature", "enough")},
                "by_threshold": rows}

    def show_waterfall(self) -> dict:
        from ..engine.waterfall import load_plan, parse_steps
        from ..engine.waterfall_report import plan_svg
        from .meetings import light_only
        o = self.office
        steps = parse_steps(load_plan(o.project, o.settings.modelling.waterfall)["steps"])
        rows = [{"#": k + 1, "langkah": s.title, "aktif": "ya" if s.enabled else "tidak",
                 "kriteria": s.criterion} for k, s in enumerate(steps)]
        self._show("Rencana waterfall", light_only(plan_svg(steps)) + html_table(rows))
        return {"shown_on_board": "Rencana waterfall", "steps": rows}

    def show_columns(self, table: str | None = None) -> dict:
        df, what = B.frame(self.office, table)
        t = B.profile_table(df)
        rec = B.records(t)
        self._show(f"Kolom {what}", f'<p class="muted">{len(df):,} baris, {df.shape[1]} '
                   f"kolom</p>" + html_table(rec))
        return {"shown_on_board": f"Kolom {what}", "columns": rec}

    def assign_task(self, to: str, task: str) -> dict:
        msg = self.office.relay_message(self.agent, to, task, kind="tugas")
        self.tasks.append(msg)
        o = self.office.orders.get(msg["id"])
        return {"assigned_to": to, "task": task, "order": o["id"], "status": o["status"],
                "notes": [n["note"] for n in o["notes"]][-2:]}

    def plan_relay(self, steps: list[dict]) -> dict:
        parent = self.office.start_relay(steps, via=self.agent)
        self.tasks.append(parent)
        p = self.office.orders.get(parent["id"])
        return {"relay": p["id"], "steps": [f"{TEAM_NAME(x['to'])}: {x['task']}" for x in p["chain"]],
                "now_at_step": p.get("step", 0) + 1, "status": p["status"]}

    def ask_teammate(self, to: str, question: str) -> dict:
        if to == self.agent:
            raise ValueError("itu kamu sendiri")
        r = self.office.agents.respond(question, to=to, context=(
            f"{TEAM_NAME(self.agent)} (rekanmu) bertanya untuk menjawab Boss. Jawab ke rekanmu, "
            "singkat dan berisi fakta dari tool."), consult=False)
        self.office.events.emit("handoff", self.agent, to=to, memo="bertanya ke rekan")
        return {"from": to, "answer": r.text, "shown_on_board": [b["title"] for b in r.board]}

    # ------------------------------------------------------------- timeline (Laras)
    def _timeline_tools(self) -> None:
        tl = self.office.timeline
        S = {"type": "string"}
        ids = {"type": "string", "description": "id milestone: " + ", ".join(
            f"{m.id}={m.title}" for m in tl.items)}
        self._add("update_milestone", "UBAH milestone timeline sesuai arahan Boss: status "
                  "(not_started, in_progress, waiting_boss, done), catatan, judul, pemilik, panjang "
                  "tahap. Milestone yang ditentukan rapat (kickoff, data, target, rencana "
                  "waterfall, cut) tidak bisa ditutup manual. Timeline baru tampil di papan.",
                  {"id": ids, "status": {**S, "enum": ["not_started", "in_progress", "waiting_boss", "done"]},
                   "note": S, "title": S, "owner": {**S, "enum": ["laras", "bening", "citra", "kirana"]},
                   "length": {"type": "integer"}}, ["id"], self.update_milestone)
        self._add("add_milestone", "TAMBAH milestone baru ke timeline, mulai setelah milestone "
                  "`after`; isi `before` kalau milestone itu harus menunggu yang baru.",
                  {"title": S, "owner": {**S, "enum": ["laras", "bening", "citra", "kirana"]},
                   "after": ids, "before": ids, "length": {"type": "integer"}, "note": S},
                  ["title", "owner", "after"], self.add_milestone)
        self._add("remove_milestone", "HAPUS milestone yang ditambahkan selama proyek (id x1, x2, "
                  "...). Milestone alur utama tidak bisa dihapus.", {"id": ids}, ["id"],
                  self.remove_milestone)
        self._add("call_meeting", "PANGGIL RAPAT koordinasi sekarang (kalau tidak ada rapat yang "
                  "sedang terbuka): topik, peserta, dan agenda singkat. Boss tinggal klik "
                  "'Masuk rapat'.", {"topic": S, "attendees": {"type": "array", "items": {
                      **S, "enum": ["laras", "bening", "citra", "kirana"]}}, "agenda": S},
                  ["topic", "attendees"], self.call_meeting)

    def _timeline_changed(self, what: str) -> dict:
        from .timeline import gantt_svg
        tl = self.office.timeline
        self._show("Timeline proyek", gantt_svg(tl))
        return {"done": what, "timeline": [{"id": m.id, "title": m.title, "owner": m.owner,
                                            "status": m.status, "note": m.note} for m in tl.items],
                "shown_on_board": "Timeline proyek"}

    def update_milestone(self, id: str, status: str | None = None, note: str | None = None,
                         title: str | None = None, owner: str | None = None,
                         length: int | None = None) -> dict:
        m = self.office.timeline.update(id, status, note, title, owner, length)
        self.office.project.audit(self.agent, "timeline_edit", {"id": id, "status": status,
                                                                 "note": note, "title": title})
        return self._timeline_changed(f"{m.title} diperbarui")

    def add_milestone(self, title: str, owner: str, after: str, before: str | None = None,
                      length: int = 1, note: str = "") -> dict:
        m = self.office.timeline.add(title, owner, after, before or None, length, note)
        self.office.project.audit(self.agent, "timeline_add", {"id": m.id, "title": title})
        return self._timeline_changed(f"{m.title} ditambahkan (id {m.id})")

    def remove_milestone(self, id: str) -> dict:
        m = self.office.timeline.remove(id)
        self.office.project.audit(self.agent, "timeline_remove", {"id": id, "title": m.title})
        return self._timeline_changed(f"{m.title} dihapus")

    def call_meeting(self, topic: str, attendees: list[str], agenda: str = "") -> dict:
        r = self.office.call_meeting(topic, attendees, agenda, host=self.agent)
        return {"meeting_id": r["meeting_id"], "status": "undangan terkirim; Boss tinggal masuk rapat"}

    def _propose(self, kind: str, payload: dict, reason: str) -> dict:
        p = self.office.proposals.add(kind, payload, self.agent, reason)
        self.created.append(p.id)
        return {"proposal_id": p.id, "summary": p.summary, "status": "menunggu ACC Boss"}

    def what_if_bins(self, feature: str, edges: list[float]) -> dict:
        X, y = self.office.train_sample()
        if feature not in X.columns:
            raise KeyError(f"no feature {feature}")
        if not is_numeric(X[feature]):
            raise ValueError(f"{feature} is categorical; edges only work for numeric features")
        auto = bin_feature(X[feature], y, name=feature)
        b = bin_numeric(X[feature], y, name=feature, edges=edges, monotonic=None)
        cols = ["bin", "count", "bad_rate", "woe"]
        return {"feature": feature, "edges": edges, "iv": round(b.iv, 4),
                "monotonic": b.is_monotonic, "bins": _records(b.table[cols]),
                "auto_iv": round(auto.iv, 4), "auto_bins": _records(auto.table[cols])}

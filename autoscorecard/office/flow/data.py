"""Data access for the team and the tools: dataset, target, cut artifacts, summaries."""
from __future__ import annotations

import json

import pandas as pd

from ...engine import data as D
from ...engine.dictionary import DataDictionary
from .. import derived as DV, prep
from ..characters import TEAM
from .common import _DATA


class DataMixin:
    def dataset(self) -> tuple[pd.DataFrame, pd.Series | None]:
        pid = self.project.id
        sig = DV.signature(self.project)
        if pid not in _DATA or _DATA[pid].get("sig") != sig:   # Bening added a variable
            p = self.project.dir("work") / "dataset.parquet"
            if not p.exists():
                raise FileNotFoundError("dataset belum dibuat (join belum di-ACC)")
            _DATA[pid] = {"df": DV.apply_all(pd.read_parquet(p), DV.load(self.project)),
                          "y": None, "sig": sig}
        d = _DATA[pid]
        st = self.state
        if d["y"] is None and st.target:
            d["y"] = D.make_target(d["df"], st.target["column"], st.target["rule"],
                                   st.target["value"])
        return d["df"], d["y"]

    def columns(self) -> list[str]:
        try:
            return list(self.dataset()[0].columns)
        except FileNotFoundError:
            return []

    def train_sample(self) -> tuple[pd.DataFrame, pd.Series]:
        df, y = self.dataset()
        if y is None:
            raise ValueError("target belum ditentukan")
        t = self.state.target
        s = D.split(df, y, date_col=t.get("date_col"), oot_from=t.get("oot_from"))
        return df.loc[s["train"]], y.loc[s["train"]].astype(int)

    def _cut_dir(self, cut: str | None = None):
        st = self.state
        built = [c for c, info in st.cuts.items() if info.get("status") in ("built", "confirmed")]
        cut = cut or (built[-1] if built else None)
        if cut is None:
            raise FileNotFoundError("belum ada cut yang selesai dibangun")
        return self.project.dir("artifacts") / cut

    # ================================================================= tool backends
    def status_summary(self) -> dict:
        st = self.state
        return {"stage": st.stage,
                "timeline": [{"id": m.id, "title": m.title, "owner": m.owner, "status": m.status,
                              "note": m.note, "depends_on": m.depends_on}
                             for m in self.timeline.items],
                "open_orders": [{"id": o["id"], "to": o["to"], "summary": o["summary"],
                                 "status": o["status"]} for o in self.orders.open_for()],
                "open_meeting": st.open_meeting,
                "pending_proposals": [p.summary for p in self.proposals.list("pending")],
                "cuts": {c: {k: v for k, v in i.items() if k in ("status", "gini_oot",
                                                                  "gini_test", "n_features")}
                         for c, i in st.cuts.items()}}

    def team_work(self, agent: str | None = None, order: int | None = None) -> dict:
        """What a teammate is working on: her open tasks with the latest notes, what she
        finished last, the relays she is a step of, and her milestones on the timeline."""
        def row(o: dict) -> dict:
            r = {"id": o["id"], "to": TEAM[o["to"]].name, "from": TEAM[o["via"]].name
                 if o.get("via") in TEAM else o.get("via"), "summary": o["summary"],
                 "status": o["status"], "notes": [n["note"] for n in o["notes"]][-3:]}
            if o.get("chain"):
                k = o.get("step", 0)
                r["relay"] = [f"{i + 1}. {TEAM[c['to']].name}: {c['task']}"
                              + (" (selesai)" if i < k or o["status"] == "selesai" else
                                 " (sekarang)" if i == k else "") for i, c in enumerate(o["chain"])]
            if o.get("parent"):
                r["step_of_relay"] = o["parent"]
            return r
        items = self.orders.list()
        if order is not None:
            o = next((x for x in items if x["id"] == int(order)), None)
            if o is None:
                raise KeyError(f"perintah/tugas #{order} tidak ada")
            out = row(o)
            if o.get("children"):
                out["steps"] = [row(x) for x in items if x["id"] in o["children"]]
            return out
        if agent is not None and agent not in TEAM:
            raise ValueError(f"tidak ada anggota tim '{agent}'")
        who = [agent] if agent else list(TEAM)
        out = {}
        for a in who:
            mine = [x for x in items if x["to"] == a]
            out[TEAM[a].name] = {
                "open": [row(x) for x in mine if x["status"] != "selesai"],
                "recently_done": [row(x) for x in mine if x["status"] == "selesai"][-3:],
                "milestones": [{"id": m.id, "title": m.title, "status": m.status, "note": m.note}
                               for m in self.timeline.items if m.owner == a and m.status != "done"]}
        return out

    def tables_summary(self) -> dict:
        tables, dicts = prep.split_uploads(self.project)
        out = {"dictionary_files": [p.name for p in dicts], "tables": {}}
        for n, t in tables.items():
            prof = D.profile(t)[["column", "dtype", "role", "missing_pct", "n_unique"]]
            out["tables"][n] = {"rows": len(t), "columns": prof.round(4).to_dict("records")}
        return out

    def target_summary(self) -> dict:
        st = self.state
        if not st.target:
            return {"target": None}
        df, y = self.dataset()
        dates = df[st.target["date_col"]] if st.target.get("date_col") else None
        a = D.assess_target(y, dates, "Q")
        return {"target": {k: v for k, v in st.target.items() if k != "candidates"}, **a}

    def feature_status(self, feature: str) -> dict:
        j = pd.read_csv(self._cut_dir() / "waterfall_features.csv").set_index("feature")
        if feature not in j.index:
            raise KeyError(f"{feature} bukan kandidat fitur")
        iv = pd.read_csv(self._cut_dir() / "iv_table.csv").set_index("feature")["iv"]
        r = j.loc[feature]
        return {"feature": feature, "status": r["status"], "dropped_at": r["dropped_at"],
                "reason": r["reason"], "iv": None if pd.isna(iv.get(feature)) else
                round(float(iv[feature]), 4), "missing_rate": round(float(r["missing_rate"]), 4)}

    def iv_summary(self) -> list[dict]:
        t = pd.read_csv(self._cut_dir() / "iv_table.csv")
        t = t.sort_values("iv", ascending=False, na_position="last")
        return t[["feature", "iv", "status", "dropped_at"]].fillna("").round(4).to_dict("records")

    def bin_summary(self, feature: str) -> dict:
        sc = json.loads((self._cut_dir() / "scorecard.json").read_text(encoding="utf-8"))
        if feature in sc["binnings"]:
            t = pd.DataFrame(sc["binnings"][feature]["table"])
            pts = pd.read_csv(self._cut_dir() / "scorecard_table.csv")
            pts = pts[pts.feature == feature].set_index("bin")["points"]
            t["points"] = t["bin"].map(pts)
            return {"feature": feature, "in_model": True, "iv": round(float(t["iv"].sum()), 4),
                    "bins": t[["bin", "count", "bad_rate", "woe", "points"]].round(4)
                    .to_dict("records")}
        from ...engine.binning import bin_feature
        X, y = self.train_sample()
        b = bin_feature(X[feature], y, name=feature)
        return {"feature": feature, "in_model": False, "iv": round(b.iv, 4),
                "bins": b.table[["bin", "count", "bad_rate", "woe"]].round(4).to_dict("records")}

    def performance_summary(self) -> dict:
        out = {}
        for c in self.state.cuts:
            p = self.project.dir("artifacts") / c / "performance.json"
            if p.exists():
                out[c] = json.loads(p.read_text(encoding="utf-8"))
        return out

    def dictionary_gaps(self) -> dict:
        df, y = self.dataset()
        t = DataDictionary.load(self.project).build(df)
        gaps = t[(t.description == "(not provided)") | (t.variable_type_source == "guessed")]
        return {"n_gaps": len(gaps), "variables": gaps[["variable", "description",
                                                        "variable_type"]].to_dict("records")}

    def cost_status(self) -> dict:
        b, led = self.settings.budget, self.router.ledger
        return {"spent_total_usd": round(led.total_usd, 4), "budget_total_usd": b.total_usd,
                "spent_project_usd": round(led.project_usd(self.project.id), 4),
                "budget_project_usd": b.per_project_usd, "ask_above_usd": b.ask_above_usd}

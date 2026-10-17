"""Tools the agents can call. Read-only tools return aggregates (profiles, bin
tables, IV, metrics), never rows. Tools that change the model only create a
proposal; the Boss's ACC applies it.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from ..engine.binning import bin_feature, bin_numeric, is_numeric
from ..llm.types import ToolSpec

MAX_CHARS = 4000


def _json(obj: Any) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS] + '..."(truncated)"'


def _records(df: pd.DataFrame, n: int = 30) -> list[dict]:
    return df.head(n).round(4).to_dict("records")


class ToolBox:
    def __init__(self, office, agent: str):
        self.office, self.agent = office, agent
        self.created: list[str] = []          # proposal ids made during this turn
        self._tools: dict[str, tuple[ToolSpec, Callable[..., Any]]] = {}
        self._register()

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
        self._add("cost_status", "Budget LLM terpakai dan sisa.", {}, [], o.cost_status)
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
        self._add("propose_plan_change", "USULKAN ubah parameter langkah waterfall (butuh ACC).",
                  {"step": S, "params": {"type": "object"}, **reason}, ["step", "params"],
                  lambda step, params, reason="": self._propose(
                      "plan_change", {"step": step, "params": params}, reason))

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

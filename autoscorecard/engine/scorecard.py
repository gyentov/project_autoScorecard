"""Points scaling and the final scorecard.

Scaling (higher score = lower risk):
    score  = offset + factor * ln(odds_good)
    factor = pdo / ln(2)                      # +pdo points doubles the good:bad odds
    offset = base_score - factor * ln(base_odds)

With the model logit(P(bad)) = b0 + sum_i b_i * WoE_i, ln(odds_good) = -logit, so

    score = sum_i points_i,   points_i = -(b_i * WoE_i + b0 / k) * factor + offset / k

for k features. The intercept and offset are spread evenly over the features, so
the attribute points add up to the total score with no separate base points.
Points are rounded per attribute; the rounded total is what a scorer would use.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .binning import VariableBinning
from .logistic import LogitResult


@dataclass
class Scaling:
    base_score: float = 600
    base_odds: float = 50          # good:bad odds at base_score (50:1 = 1.96% bad)
    pdo: float = 20

    @property
    def factor(self) -> float:
        return self.pdo / math.log(2)

    @property
    def offset(self) -> float:
        return self.base_score - self.factor * math.log(self.base_odds)

    def score_from_pd(self, p_bad):
        p = np.clip(np.asarray(p_bad, dtype=float), 1e-12, 1 - 1e-12)
        return self.offset + self.factor * np.log((1 - p) / p)

    def pd_from_score(self, score):
        odds_good = np.exp((np.asarray(score, dtype=float) - self.offset) / self.factor)
        return 1 / (1 + odds_good)


@dataclass
class Scorecard:
    binnings: dict[str, VariableBinning]
    model: LogitResult
    scaling: Scaling
    meta: dict = field(default_factory=dict)     # build_id, status (draft / confirmed), ...

    @property
    def features(self) -> list[str]:
        return self.model.features

    def table(self) -> pd.DataFrame:
        """The scorecard: one row per feature x bin with WoE, coefficient and points."""
        k = len(self.features)
        b0 = self.model.coef["intercept"]
        f, off = self.scaling.factor, self.scaling.offset
        rows = []
        for feat in self.features:
            bi = self.model.coef[feat]
            for r in self.binnings[feat].table.itertuples():
                raw = -(bi * r.woe + b0 / k) * f + off / k
                rows.append({"feature": feat, "bin": r.bin, "count": r.count, "pct": r.pct,
                             "bad_rate": r.bad_rate, "woe": r.woe,
                             "woe_edited": bool(getattr(r, "woe_edited", False)), "coef": bi,
                             "points_raw": raw, "points": int(round(raw))})
        return pd.DataFrame(rows)

    def points(self, df: pd.DataFrame) -> pd.DataFrame:
        """Points per feature for each row (columns = features), rounded as in table()."""
        t = self.table()
        out = {}
        neutral = {}
        k = len(self.features)
        for feat in self.features:
            lookup = dict(zip(t.loc[t.feature == feat, "bin"], t.loc[t.feature == feat, "points"]))
            # a value with no training bin gets WoE 0 -> the points of WoE 0
            neutral[feat] = int(round(-(self.model.coef["intercept"] / k) * self.scaling.factor
                                      + self.scaling.offset / k))
            labels = self.binnings[feat].assign(df[feat])
            out[feat] = labels.map(lambda b, lk=lookup, n=neutral[feat]: lk.get(b, n)).astype(int)
        return pd.DataFrame(out, index=df.index)

    def score(self, df: pd.DataFrame) -> pd.Series:
        return self.points(df).sum(axis=1).rename("score")

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        X = pd.DataFrame({f: self.binnings[f].transform(df[f]) for f in self.features},
                         index=df.index)
        return self.model.predict_proba(X)

    def to_dict(self) -> dict:
        return {"meta": self.meta, "scaling": vars(self.scaling), "model": self.model.to_dict(),
                "binnings": {f: self.binnings[f].to_dict() for f in self.features}}

    def save(self, path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, default=float)

    @classmethod
    def load(cls, path) -> "Scorecard":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        m = d["model"]
        model = LogitResult(m["features"], m["coef"], m["std_err"], m["z"], m["p_value"],
                            m["log_likelihood"], m["n_obs"], m["iterations"], m["converged"],
                            m.get("warnings", []))
        return cls({f: VariableBinning.from_dict(b) for f, b in d["binnings"].items()},
                   model, Scaling(**d["scaling"]), d.get("meta", {}))

"""Logistic regression fitted by IRLS (Newton-Raphson), written out in full.

Model:  logit(P(bad=1)) = b0 + b1*x1 + ... + bk*xk

Each Newton step solves (X' W X) delta = X' (y - p), with W = diag(p(1-p)).
At convergence, the covariance of the coefficients is inv(X' W X), which gives
the standard errors, Wald z statistics and p-values reported in the summary.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def norm_sf(z: float) -> float:
    """Upper tail of the standard normal: P(Z > z)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def chi2_sf_df1(x: float) -> float:
    """Upper tail of chi-square with 1 degree of freedom (used for LR tests)."""
    return math.erfc(math.sqrt(max(x, 0.0) / 2.0))


@dataclass
class LogitResult:
    features: list[str]
    coef: dict[str, float]           # includes "intercept"
    std_err: dict[str, float]
    z: dict[str, float]
    p_value: dict[str, float]
    log_likelihood: float
    n_obs: int
    iterations: int
    converged: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def aic(self) -> float:
        return 2 * (len(self.features) + 1) - 2 * self.log_likelihood

    @property
    def bic(self) -> float:
        return math.log(self.n_obs) * (len(self.features) + 1) - 2 * self.log_likelihood

    def summary(self) -> pd.DataFrame:
        names = ["intercept"] + self.features
        return pd.DataFrame({
            "coef": [self.coef[n] for n in names],
            "std_err": [self.std_err[n] for n in names],
            "z": [self.z[n] for n in names],
            "p_value": [self.p_value[n] for n in names],
        }, index=pd.Index(names, name="term"))

    def predict_logit(self, X: pd.DataFrame) -> np.ndarray:
        out = np.full(len(X), self.coef["intercept"], dtype=float)
        for f in self.features:
            out += self.coef[f] * X[f].to_numpy(dtype=float)
        return out

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.predict_logit(X)))

    def to_dict(self) -> dict:
        return {"features": self.features, "coef": self.coef, "std_err": self.std_err,
                "z": self.z, "p_value": self.p_value, "log_likelihood": self.log_likelihood,
                "aic": self.aic, "bic": self.bic, "n_obs": self.n_obs,
                "iterations": self.iterations, "converged": self.converged,
                "warnings": self.warnings}


def fit_logit(X: pd.DataFrame, y, max_iter: int = 50, tol: float = 1e-8) -> LogitResult:
    """Fit logit(P(y=1)) on the columns of X (plus an intercept)."""
    features = list(X.columns)
    y = np.asarray(y, dtype=float)
    if not set(np.unique(y)) <= {0.0, 1.0}:
        raise ValueError("target must be 0/1")
    A = np.column_stack([np.ones(len(y))] + [X[f].to_numpy(dtype=float) for f in features])
    if np.isnan(A).any():
        raise ValueError("X contains missing values; WoE-transform first")

    # Start from the intercept-only solution: b0 = logit(mean bad rate).
    beta = np.zeros(A.shape[1])
    ybar = y.mean()
    beta[0] = math.log(ybar / (1 - ybar))
    warnings: list[str] = []
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        eta = np.clip(A @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = p * (1 - p)
        H = A.T @ (A * w[:, None])                      # X' W X
        g = A.T @ (y - p)                               # score vector
        try:
            delta = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            delta = np.linalg.lstsq(H, g, rcond=None)[0]
            warnings.append("singular X'WX (perfectly collinear features?)")
        beta = beta + delta
        if np.max(np.abs(delta)) < tol:
            converged = True
            break
    if not converged:
        warnings.append(f"did not converge in {max_iter} iterations (separation?)")

    eta = np.clip(A @ beta, -30, 30)
    p = 1.0 / (1.0 + np.exp(-eta))
    eps = 1e-15
    ll = float(np.sum(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))
    H = A.T @ (A * (p * (1 - p))[:, None])
    cov = np.linalg.pinv(H)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))

    names = ["intercept"] + features
    coef = {n: float(b) for n, b in zip(names, beta)}
    std_err = {n: float(s) for n, s in zip(names, se)}
    z = {n: (coef[n] / std_err[n] if std_err[n] > 0 else float("nan")) for n in names}
    pval = {n: (2 * norm_sf(abs(z[n])) if not math.isnan(z[n]) else float("nan")) for n in names}
    return LogitResult(features, coef, std_err, z, pval, ll, len(y), it, converged, warnings)

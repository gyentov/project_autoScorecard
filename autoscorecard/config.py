"""Load config/config.yaml plus API keys from the environment (.env supported)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"


class Price(BaseModel):
    """USD per 1M tokens."""
    input: float = 0.0
    output: float = 0.0
    cache_write: float | None = None   # defaults to input price
    cache_read: float | None = None    # defaults to input price

    @property
    def is_free(self) -> bool:
        return self.input == 0 and self.output == 0


class ProviderConfig(BaseModel):
    type: Literal["anthropic", "openai_compat", "offline"]
    model: str = ""
    key_env: str | None = None
    base_url: str | None = None
    max_tokens: int = 4096
    rpm_limit: int | None = None
    price: Price = Field(default_factory=Price)

    def api_key(self) -> str | None:
        if not self.key_env:
            return None
        return os.environ.get(self.key_env) or None

    @property
    def available(self) -> bool:
        return self.type == "offline" or self.api_key() is not None


class AppConfig(BaseModel):
    projects_dir: Path = Path("projects")
    ledger_path: Path = Path("data/ledger/usage.jsonl")
    agent_language: Literal["id", "en"] = "id"
    deck_language: Literal["id", "en"] = "en"


class BudgetConfig(BaseModel):
    total_usd: float = 4.50
    per_project_usd: float = 1.00
    ask_above_usd: float = 0.02     # Laras asks the Boss before work that may cost more


class ModellingConfig(BaseModel):
    # folders (or dotted modules) with custom waterfall steps, loaded before the plan
    step_plugins: list[str] = Field(default_factory=lambda: ["plugins/steps"])
    waterfall: list[dict] | None = None   # None = engine default (DEFAULT_WATERFALL)
    outliers: dict = Field(default_factory=lambda: {"method": "iqr", "k": 1.5,
                                                    "treatment": "none"})
    # hyperparameter tuning of the final cut (see engine/tuning.py)
    tuning: dict = Field(default_factory=lambda: {
        "enabled": True, "val_size": 0.25, "max_gap": 0.05,
        "grid": {"min_bin_pct": [0.03, 0.05, 0.08], "max_bins": [5, 8], "p_enter": [0.01, 0.05]}})

    @model_validator(mode="after")
    def _check_waterfall(self) -> "ModellingConfig":
        from autoscorecard.engine.steps import load_plugins
        from autoscorecard.engine.waterfall import parse_steps
        load_plugins(self.step_plugins, root=ROOT)
        parse_steps(self.waterfall)            # raises on unknown steps / bad params
        from autoscorecard.engine.dictionary import OutlierRule
        OutlierRule(**self.outliers)
        return self


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    modelling: ModellingConfig = Field(default_factory=ModellingConfig)
    providers: dict[str, ProviderConfig]
    agents: dict[str, str] = Field(default_factory=dict)
    fallback_chain: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_refs(self) -> "Settings":
        for name in [*self.agents.values(), *self.fallback_chain]:
            if name not in self.providers:
                raise ValueError(f"unknown provider '{name}' referenced in config")
        return self

    def resolve_paths(self, root: Path) -> "Settings":
        for attr in ("projects_dir", "ledger_path"):
            p = getattr(self.app, attr)
            if not p.is_absolute():
                setattr(self.app, attr, root / p)
        return self


def load_settings(path: Path | str | None = None, env_file: Path | str | None = None) -> Settings:
    load_dotenv(env_file or ROOT / ".env", override=False)
    path = Path(path) if path else DEFAULT_CONFIG
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Settings.model_validate(data).resolve_paths(ROOT)

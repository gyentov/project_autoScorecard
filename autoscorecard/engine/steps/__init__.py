"""Waterfall steps. Importing this package registers the built-in steps; custom
steps are loaded from plugins/steps/ (see base.py for how to write one)."""
from .base import (REGISTRY, BinningCache, StepContext, StepResult, StepType, feature_stats,
                   load_plugins, register_step)
from . import filters, correlation, model_selection, finalize  # noqa: F401,I001  (registration, in waterfall order)

BUILTIN_ORDER = ["missing_rate", "identical_rate", "iv", "correlation_cluster", "stepwise",
                 "forward", "backward", "finalization"]

__all__ = ["REGISTRY", "BinningCache", "StepContext", "StepResult", "StepType", "feature_stats",
           "load_plugins", "register_step", "BUILTIN_ORDER"]

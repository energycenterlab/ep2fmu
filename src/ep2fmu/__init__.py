"""Public API for ep2fmu."""

__version__ = "1.0.0"

from ep2fmu.api import build_fmu, validate_model
from ep2fmu.models import (
    BuildConfig,
    BuildOptions,
    BuildResult,
    InputMapping,
    OutputMapping,
    ValidationReport,
)

__all__ = [
    "BuildConfig",
    "BuildOptions",
    "BuildResult",
    "InputMapping",
    "OutputMapping",
    "ValidationReport",
    "build_fmu",
    "validate_model",
]

"""Public package surface for ep2fmu.

Import from this module when using ep2fmu as a library. It exposes the stable
build and validation entry points together with the typed configuration and
result models.
"""

__version__ = "1.0.0"

from ep2fmu.api import build_fmu, validate_model
from ep2fmu.models import (
    BuildConfig,
    BuildOptions,
    BuildResult,
    InputMapping,
    OutputMapping,
    ValidationIssue,
    ValidationReport,
)

__all__ = [
    "BuildConfig",
    "BuildOptions",
    "BuildResult",
    "InputMapping",
    "OutputMapping",
    "ValidationIssue",
    "ValidationReport",
    "build_fmu",
    "validate_model",
]

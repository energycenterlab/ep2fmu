from __future__ import annotations

from ep2fmu import (
    BuildConfig,
    BuildOptions,
    BuildResult,
    InputMapping,
    OutputMapping,
    ValidationIssue,
    ValidationReport,
    __all__ as public_names,
    build_fmu,
    validate_model,
)


def test_public_api_exports_expected_symbols() -> None:
    expected = {
        "BuildConfig",
        "BuildOptions",
        "BuildResult",
        "InputMapping",
        "OutputMapping",
        "ValidationIssue",
        "ValidationReport",
        "build_fmu",
        "validate_model",
    }
    assert expected.issubset(set(public_names))
    assert BuildConfig.__name__ == "BuildConfig"
    assert BuildOptions.__name__ == "BuildOptions"
    assert BuildResult.__name__ == "BuildResult"
    assert InputMapping.__name__ == "InputMapping"
    assert OutputMapping.__name__ == "OutputMapping"
    assert ValidationIssue.__name__ == "ValidationIssue"
    assert ValidationReport.__name__ == "ValidationReport"
    assert callable(build_fmu)
    assert callable(validate_model)

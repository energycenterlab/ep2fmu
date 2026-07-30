from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def sample_epjson() -> dict[str, object]:
    return {
        "Version": {"Version 1": {"version_identifier": "26.1"}},
        "Timestep": {"Timestep 1": {"number_of_timesteps_per_hour": 4}},
        "RunPeriod": {
            "Annual": {
                "begin_month": 1,
                "begin_day_of_month": 1,
                "end_month": 1,
                "end_day_of_month": 2,
            }
        },
        "ExternalInterface": {
            "FunctionalMockupUnitExport": {
                "name_of_external_interface": "FunctionalMockupUnitExport"
            }
        },
        "ExternalInterface:FunctionalMockupUnitExport:From:Variable": {
            "Zone temperature mapping": {
                "output_variable_index_key_name": "Zone 1",
                "output_variable_name": "Zone Mean Air Temperature",
                "fmu_variable_name": "zoneTemperature",
            }
        },
        "ExternalInterface:FunctionalMockupUnitExport:To:Schedule": {
            "Heating Setpoint": {
                "schedule_type_limits_names": "Temperature",
                "fmu_variable_name": "heatingSetpoint",
                "initial_value": 20.0,
            }
        },
    }


@pytest.fixture
def sample_model(tmp_path: Path, sample_epjson: dict[str, object]) -> Path:
    path = tmp_path / "building.epJSON"
    path.write_text(json.dumps(sample_epjson), encoding="utf-8")
    return path


@pytest.fixture
def dummy_runtimes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runtimes"
    for platform, extension in {
        "linux64": ".so",
        "win64": ".dll",
        "darwin64": ".dylib",
        "darwinarm64": ".dylib",
    }.items():
        directory = root / platform
        directory.mkdir(parents=True)
        binary_platform = "darwin-universal" if platform.startswith("darwin") else platform
        (directory / f"ep2fmu_fmi2{extension}").write_bytes(f"library-{binary_platform}".encode())
        worker = "ep2fmu-worker.exe" if platform == "win64" else "ep2fmu-worker"
        (directory / worker).write_bytes(f"worker-{binary_platform}".encode())
    monkeypatch.setenv("EP2FMU_RUNTIME_DIR", str(root))
    return root

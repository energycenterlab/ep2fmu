from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest
from fmpy import extract
from fmpy.fmi2 import FMU2Slave

from ep2fmu.constants import Platform
from ep2fmu.metadata import build_model_description
from ep2fmu.models import BuildConfig, OutputKind, OutputMapping
from ep2fmu.packaging import runtime_config, write_fmu
from ep2fmu.runtime_assets import load_runtime_asset


def current_platform() -> Platform:
    if platform.system() == "Darwin":
        return Platform.DARWINARM64 if platform.machine() == "arm64" else Platform.DARWIN64
    if platform.system() == "Windows":
        return Platform.WIN64
    return Platform.LINUX64


@pytest.mark.skipif(
    os.environ.get("EP2FMU_NATIVE_RUNTIME_TEST") != "1",
    reason="requires a compiled runtime bundle for the current platform",
)
def test_fmpy_can_instantiate_native_bridge(tmp_path: Path) -> None:
    identifier = "NativeBridge"
    guid = "{00000000-0000-0000-0000-000000000001}"
    config = BuildConfig(
        outputs=(
            OutputMapping(
                name="temperature",
                kind=OutputKind.VARIABLE,
                key="Zone 1",
                variable="Zone Mean Air Temperature",
            ),
        )
    )
    fmu = tmp_path / "native.fmu"
    write_fmu(
        fmu,
        model_identifier=identifier,
        model_description=build_model_description(
            model_name=identifier,
            model_identifier=identifier,
            guid=guid,
            config=config,
            stop_time=86400.0,
            generation_tool="ep2fmu native test",
        ),
        model_epjson=b'{"Version":{"Version 1":{"version_identifier":"26.1"}}}\n',
        config_json=runtime_config(
            model_file="model.epJSON",
            weather_file=None,
            zone_step_seconds=900.0,
            stop_time_seconds=86400.0,
            inputs=[],
            outputs=[
                {
                    "name": "temperature",
                    "kind": "variable",
                    "key": "Zone 1",
                    "variable": "Zone Mean Air Temperature",
                }
            ],
        ),
        weather_name=None,
        weather_data=None,
        runtime_assets=(load_runtime_asset(current_platform()),),
    )
    directory = extract(str(fmu), unzipdir=str(tmp_path / "unpacked"))
    slave = FMU2Slave(
        guid=guid,
        unzipDirectory=directory,
        modelIdentifier=identifier,
        instanceName="native-test",
    )
    slave.instantiate()
    assert slave.getVersion() == "2.0"
    slave.freeInstance()

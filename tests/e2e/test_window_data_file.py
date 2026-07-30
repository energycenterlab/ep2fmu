from __future__ import annotations

import json
import os
import platform
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fmpy import simulate_fmu
from fmpy.validation import validate_fmu

from ep2fmu import BuildOptions, build_fmu
from ep2fmu.constants import Platform


def current_platform() -> Platform:
    if platform.system() == "Darwin":
        return Platform.DARWINARM64 if platform.machine() == "arm64" else Platform.DARWIN64
    if platform.system() == "Windows":
        return Platform.WIN64
    return Platform.LINUX64


@pytest.mark.skipif(
    os.environ.get("EP2FMU_E2E") != "1",
    reason="set EP2FMU_E2E=1 and ENERGYPLUS_HOME to run the EnergyPlus integration",
)
def test_window_data_file_is_packaged_and_used_at_runtime(tmp_path: Path) -> None:
    home = Path(os.environ["ENERGYPLUS_HOME"])
    source_model = home / "ExampleFiles" / "1ZoneUncontrolled_win_1.idf"
    source_window_data = home / "DataSets" / "Window5DataFile.dat"
    model = tmp_path / "OneZoneWindowDataFile.idf"
    model_text = source_model.read_text(encoding="utf-8")
    window_path = r"..\datasets\Window5DataFile.dat"
    disabled_weather_run = (
        "    No,                      !- Run Simulation for Weather File Run Periods"
    )
    assert window_path in model_text
    assert disabled_weather_run in model_text
    model.write_text(
        model_text.replace(window_path, "Window5DataFile.dat").replace(
            disabled_weather_run,
            "    Yes,                     !- Run Simulation for Weather File Run Periods",
        ),
        encoding="utf-8",
    )
    window_data = tmp_path / "Window5DataFile.dat"
    window_data.write_bytes(source_window_data.read_bytes())

    weather = home / "WeatherData" / "USA_CO_Golden-NREL.724666_TMY3.epw"
    config = Path(__file__).with_name("indoor_outputs.ep2fmu.yaml")
    fmu = tmp_path / "window-data-file.fmu"
    build_fmu(
        BuildOptions(
            model_path=model,
            weather_path=weather,
            config_path=config,
            energyplus_home=home,
            output_path=fmu,
            platforms=(current_platform(),),
        )
    )

    assert validate_fmu(str(fmu)) == []
    with zipfile.ZipFile(fmu) as archive:
        assert archive.read("resources/Window5DataFile.dat") == window_data.read_bytes()
        packaged_model = json.loads(archive.read("resources/model.epJSON"))
    construction = packaged_model["Construction:WindowDataFile"]["DoubleClear"]
    assert construction["file_name"] == "Window5DataFile.dat"

    output_directory = tmp_path / "energyplus-output"
    result = simulate_fmu(
        str(fmu),
        start_time=0.0,
        stop_time=86400.0,
        step_size=900.0,
        output_interval=900.0,
        output=["indoorTemperature", "indoorRelativeHumidity"],
        start_values={
            "energyplusHome": str(home),
            "outputDirectory": str(output_directory),
            "keepOutputs": True,
        },
    )

    assert np.isfinite(result["indoorTemperature"]).all()
    assert np.isfinite(result["indoorRelativeHumidity"]).all()
    assert "Completed Successfully" in (output_directory / "eplusout.end").read_text(
        encoding="utf-8"
    )

from __future__ import annotations

import os
import platform
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
def test_annual_indoor_temperature_and_humidity(tmp_path: Path) -> None:
    home = Path(os.environ["ENERGYPLUS_HOME"])
    model = home / "ExampleFiles" / "1ZoneUncontrolled.idf"
    weather = home / "WeatherData" / "USA_CO_Golden-NREL.724666_TMY3.epw"
    config = Path(__file__).with_name("indoor_outputs.ep2fmu.yaml")
    fmu = tmp_path / "OneZoneIndoorConditions.fmu"

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

    stop_time = 365 * 86400.0
    result = simulate_fmu(
        str(fmu),
        start_time=0.0,
        stop_time=stop_time,
        step_size=86400.0,
        output_interval=86400.0,
        output=["indoorTemperature", "indoorRelativeHumidity"],
        start_values={
            "energyplusHome": str(home),
            "keepOutputs": True,
            "outputDirectory": str(tmp_path / "energyplus-output"),
        },
    )

    temperature = result["indoorTemperature"]
    humidity = result["indoorRelativeHumidity"]
    assert result["time"][-1] == stop_time
    assert np.isfinite(temperature).all()
    assert np.isfinite(humidity).all()
    assert np.ptp(temperature) > 1.0
    assert np.ptp(humidity) > 1.0
    assert (humidity >= 0.0).all()
    assert (humidity <= 100.0).all()
    assert "Completed Successfully" in (tmp_path / "energyplus-output" / "eplusout.end").read_text(
        encoding="utf-8"
    )

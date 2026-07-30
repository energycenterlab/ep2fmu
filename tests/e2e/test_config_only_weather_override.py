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

INFILTRATION_ADDITION = """

ZoneInfiltration:DesignFlowRate,
  Config Only Test Infiltration,  !- Name
  ZONE ONE,                       !- Zone Name
  AlwaysOn,                       !- Schedule Name
  Flow/Zone,                      !- Design Flow Rate Calculation Method
  0.10,                           !- Design Flow Rate {m3/s}
  ,                               !- Flow Rate per Floor Area
  ,                               !- Flow Rate per Exterior Surface Area
  ,                               !- Air Changes per Hour
  1.0,                            !- Constant Term Coefficient
  0.0,                            !- Temperature Term Coefficient
  0.0,                            !- Velocity Term Coefficient
  0.0;                            !- Velocity Squared Term Coefficient
"""


def current_platform() -> Platform:
    if platform.system() == "Darwin":
        return Platform.DARWINARM64 if platform.machine() == "arm64" else Platform.DARWIN64
    if platform.system() == "Windows":
        return Platform.WIN64
    return Platform.LINUX64


def weather_inputs(
        temperature: float,
        relative_humidity: float,
        stop_time: float,
) -> np.ndarray:
    return np.array(
        [
            (0.0, temperature, relative_humidity),
            (stop_time, temperature, relative_humidity),
        ],
        dtype=[
            ("time", np.float64),
            ("outdoorTemperature", np.float64),
            ("outdoorRelativeHumidity", np.float64),
        ],
    )


def simulate_weather_case(
        fmu: Path,
        *,
        home: Path,
        temperature: float,
        relative_humidity: float,
        output_directory: Path,
) -> np.ndarray:
    stop_time = 86400.0
    return simulate_fmu(
        str(fmu),
        start_time=0.0,
        stop_time=stop_time,
        step_size=900.0,
        output_interval=900.0,
        input=weather_inputs(temperature, relative_humidity, stop_time),
        output=["indoorTemperature", "indoorRelativeHumidity"],
        start_values={
            "energyplusHome": str(home),
            "outputDirectory": str(output_directory),
            "keepOutputs": True,
        },
    )


@pytest.mark.skipif(
    os.environ.get("EP2FMU_E2E") != "1",
    reason="set EP2FMU_E2E=1 and ENERGYPLUS_HOME to run the EnergyPlus integration",
)
def test_yaml_maps_model_outputs_and_weather_inputs_without_embedded_interfaces(
        tmp_path: Path,
) -> None:
    home = Path(os.environ["ENERGYPLUS_HOME"])
    source_model = home / "ExampleFiles" / "1ZoneUncontrolled.idf"
    model_text = source_model.read_text(encoding="utf-8")
    assert "ExternalInterface" not in model_text
    assert "ZoneInfiltration:DesignFlowRate" not in model_text
    model = tmp_path / "OneZoneWithInfiltration.idf"
    model.write_text(model_text + INFILTRATION_ADDITION, encoding="utf-8")

    weather = home / "WeatherData" / "USA_CO_Golden-NREL.724666_TMY3.epw"
    config = Path(__file__).with_name("weather_override.ep2fmu.yaml")
    fmu = tmp_path / "config-only-weather-override.fmu"
    result = build_fmu(
        BuildOptions(
            model_path=model,
            weather_path=weather,
            config_path=config,
            energyplus_home=home,
            output_path=fmu,
            platforms=(current_platform(),),
        )
    )

    assert result.input_count == 2
    assert result.output_count == 2
    assert validate_fmu(str(fmu)) == []

    cold = simulate_weather_case(
        fmu,
        home=home,
        temperature=-10.0,
        relative_humidity=50.0,
        output_directory=tmp_path / "cold-output",
    )
    hot = simulate_weather_case(
        fmu,
        home=home,
        temperature=35.0,
        relative_humidity=50.0,
        output_directory=tmp_path / "hot-output",
    )
    dry = simulate_weather_case(
        fmu,
        home=home,
        temperature=20.0,
        relative_humidity=10.0,
        output_directory=tmp_path / "dry-output",
    )
    humid = simulate_weather_case(
        fmu,
        home=home,
        temperature=20.0,
        relative_humidity=90.0,
        output_directory=tmp_path / "humid-output",
    )

    for values in (cold, hot, dry, humid):
        assert np.isfinite(values["indoorTemperature"]).all()
        assert np.isfinite(values["indoorRelativeHumidity"]).all()

    comparison_slice = slice(8, None)
    assert np.mean(hot["indoorTemperature"][comparison_slice]) > (
            np.mean(cold["indoorTemperature"][comparison_slice]) + 5.0
    )
    assert np.mean(humid["indoorRelativeHumidity"][comparison_slice]) > (
            np.mean(dry["indoorRelativeHumidity"][comparison_slice]) + 10.0
    )

    output_directories = (
        tmp_path / "cold-output",
        tmp_path / "hot-output",
        tmp_path / "dry-output",
        tmp_path / "humid-output",
    )
    for directory in output_directories:
        assert "Completed Successfully" in (directory / "eplusout.end").read_text(encoding="utf-8")

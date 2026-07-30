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

IDEAL_LOADS_ADDITION = """

ScheduleTypeLimits,
  Temperature,              !- Name
  -100,                     !- Lower Limit Value
  100,                      !- Upper Limit Value
  Continuous,               !- Numeric Type
  Temperature;              !- Unit Type

ScheduleTypeLimits,
  Thermostat Control Type,  !- Name
  0,                        !- Lower Limit Value
  4,                        !- Upper Limit Value
  Discrete;                 !- Numeric Type

Schedule:Constant,
  Heating Only Control,     !- Name
  Thermostat Control Type,  !- Schedule Type Limits Name
  1;                        !- Hourly Value

Schedule:Constant,
  External Heating Setpoint,  !- Name
  Temperature,                !- Schedule Type Limits Name
  21;                         !- Hourly Value

ThermostatSetpoint:SingleHeating,
  External Heating Setpoint Object,  !- Name
  External Heating Setpoint;         !- Setpoint Temperature Schedule Name

ZoneControl:Thermostat,
  Zone One Thermostat,               !- Name
  ZONE ONE,                          !- Zone or ZoneList Name
  Heating Only Control,              !- Control Type Schedule Name
  ThermostatSetpoint:SingleHeating,  !- Control 1 Object Type
  External Heating Setpoint Object;  !- Control 1 Name

ZoneHVAC:EquipmentConnections,
  ZONE ONE,                  !- Zone Name
  ZONE ONE EQUIPMENT,        !- Zone Conditioning Equipment List Name
  IDEAL SUPPLY NODE,         !- Zone Air Inlet Node or NodeList Name
  ,                          !- Zone Air Exhaust Node or NodeList Name
  ZONE ONE AIR NODE,         !- Zone Air Node Name
  ZONE ONE RETURN NODE;      !- Zone Return Air Node or NodeList Name

ZoneHVAC:EquipmentList,
  ZONE ONE EQUIPMENT,             !- Name
  SequentialLoad,                 !- Load Distribution Scheme
  ZoneHVAC:IdealLoadsAirSystem,   !- Zone Equipment 1 Object Type
  ONE ZONE IDEAL LOADS,           !- Zone Equipment 1 Name
  1,                              !- Zone Equipment 1 Cooling Sequence
  1;                              !- Zone Equipment 1 Heating Sequence

ZoneHVAC:IdealLoadsAirSystem,
  ONE ZONE IDEAL LOADS,     !- Name
  ,                         !- Availability Schedule Name
  IDEAL SUPPLY NODE,        !- Zone Supply Air Node Name
  ,                         !- Zone Exhaust Air Node Name
  ,                         !- System Inlet Air Node Name
  50,                       !- Maximum Heating Supply Air Temperature
  13,                       !- Minimum Cooling Supply Air Temperature
  0.015,                    !- Maximum Heating Supply Air Humidity Ratio
  0.009,                    !- Minimum Cooling Supply Air Humidity Ratio
  LimitFlowRate,            !- Heating Limit
  1.0,                      !- Maximum Heating Air Flow Rate
  ,                         !- Maximum Sensible Heating Capacity
  LimitFlowRate,            !- Cooling Limit
  1.0,                      !- Maximum Cooling Air Flow Rate
  ,                         !- Maximum Total Cooling Capacity
  ,                         !- Heating Availability Schedule Name
  ,                         !- Cooling Availability Schedule Name
  ConstantSupplyHumidityRatio,  !- Dehumidification Control Type
  ,                         !- Cooling Sensible Heat Ratio
  ConstantSupplyHumidityRatio;  !- Humidification Control Type
"""


def current_platform() -> Platform:
    if platform.system() == "Darwin":
        return Platform.DARWINARM64 if platform.machine() == "arm64" else Platform.DARWIN64
    if platform.system() == "Windows":
        return Platform.WIN64
    return Platform.LINUX64


def create_ideal_loads_idf(home: Path, destination: Path) -> Path:
    reference = home / "ExampleFiles" / "1ZoneUncontrolled.idf"
    destination.write_text(
        reference.read_text(encoding="utf-8") + IDEAL_LOADS_ADDITION,
        encoding="utf-8",
    )
    return destination


def constant_input(name: str, value: float, stop_time: float) -> np.ndarray:
    return np.array(
        [(0.0, value), (stop_time, value)],
        dtype=[("time", np.float64), (name, np.float64)],
    )


def build_test_fmu(
        *,
        home: Path,
        model: Path,
        config_name: str,
        destination: Path,
) -> Path:
    weather = home / "WeatherData" / "USA_CO_Golden-NREL.724666_TMY3.epw"
    config = Path(__file__).with_name(config_name)
    build_fmu(
        BuildOptions(
            model_path=model,
            weather_path=weather,
            config_path=config,
            energyplus_home=home,
            output_path=destination,
            platforms=(current_platform(),),
        )
    )
    assert validate_fmu(str(destination)) == []
    return destination


def simulate(
        fmu: Path,
        *,
        home: Path,
        input_name: str,
        input_value: float,
        output_directory: Path,
) -> np.ndarray:
    stop_time = 86400.0
    return simulate_fmu(
        str(fmu),
        start_time=0.0,
        stop_time=stop_time,
        step_size=900.0,
        output_interval=900.0,
        input=constant_input(input_name, input_value, stop_time),
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
def test_external_schedule_controls_ideal_loads_thermostat(tmp_path: Path) -> None:
    home = Path(os.environ["ENERGYPLUS_HOME"])
    model = create_ideal_loads_idf(home, tmp_path / "OneZoneIdealLoads.idf")
    fmu = build_test_fmu(
        home=home,
        model=model,
        config_name="ideal_loads_schedule.ep2fmu.yaml",
        destination=tmp_path / "schedule-control.fmu",
    )

    low = simulate(
        fmu,
        home=home,
        input_name="heatingSetpoint",
        input_value=15.0,
        output_directory=tmp_path / "schedule-low",
    )
    high = simulate(
        fmu,
        home=home,
        input_name="heatingSetpoint",
        input_value=24.0,
        output_directory=tmp_path / "schedule-high",
    )

    assert np.isfinite(low["indoorTemperature"]).all()
    assert np.isfinite(high["indoorTemperature"]).all()
    assert np.mean(high["indoorTemperature"][8:]) > np.mean(low["indoorTemperature"][8:]) + 5.0
    assert np.max(high["sensibleHeatingRate"]) > np.max(low["sensibleHeatingRate"])


@pytest.mark.skipif(
    os.environ.get("EP2FMU_E2E") != "1",
    reason="set EP2FMU_E2E=1 and ENERGYPLUS_HOME to run the EnergyPlus integration",
)
def test_external_actuator_controls_ideal_loads_air_mass_flow(tmp_path: Path) -> None:
    home = Path(os.environ["ENERGYPLUS_HOME"])
    model = create_ideal_loads_idf(home, tmp_path / "OneZoneIdealLoads.idf")
    fmu = build_test_fmu(
        home=home,
        model=model,
        config_name="ideal_loads_actuator.ep2fmu.yaml",
        destination=tmp_path / "actuator-control.fmu",
    )

    off = simulate(
        fmu,
        home=home,
        input_name="supplyAirMassFlow",
        input_value=0.0,
        output_directory=tmp_path / "actuator-off",
    )
    forced = simulate(
        fmu,
        home=home,
        input_name="supplyAirMassFlow",
        input_value=0.25,
        output_directory=tmp_path / "actuator-forced",
    )

    assert np.max(np.abs(off["supplyNodeMassFlow"][1:])) < 1e-6
    assert np.mean(forced["supplyNodeMassFlow"][1:]) == pytest.approx(0.25, abs=1e-6)
    assert np.isfinite(forced["indoorTemperature"]).all()

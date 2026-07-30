from __future__ import annotations

from ep2fmu.config import extract_legacy_config, merge_configs, transform_epjson
from ep2fmu.models import (
    BuildConfig,
    InputKind,
    InputMapping,
    ModelMetadata,
    OutputKind,
    OutputMapping,
)


def test_extracts_legacy_objects(sample_epjson: dict[str, object]) -> None:
    config = extract_legacy_config(sample_epjson)
    assert config.inputs[0].name == "heatingSetpoint"
    assert config.inputs[0].kind == InputKind.SCHEDULE
    assert config.outputs[0].name == "zoneTemperature"
    assert config.outputs[0].kind == OutputKind.VARIABLE


def test_yaml_overlay_replaces_name_across_causalities(
        sample_epjson: dict[str, object],
) -> None:
    legacy = extract_legacy_config(sample_epjson)
    overlay = BuildConfig(
        model=ModelMetadata(name="Overridden"),
        inputs=(
            InputMapping(
                name="zoneTemperature",
                kind=InputKind.EMS_GLOBAL,
                key="Command",
                start=1.0,
            ),
        ),
    )
    merged = merge_configs(legacy, overlay)
    assert [item.name for item in merged.inputs] == ["heatingSetpoint", "zoneTemperature"]
    assert merged.outputs == ()


def test_transform_removes_transport_and_adds_api_objects(
        sample_epjson: dict[str, object],
) -> None:
    config = extract_legacy_config(sample_epjson)
    transformed = transform_epjson(sample_epjson, config)
    assert "ExternalInterface" not in transformed
    assert not any("FunctionalMockupUnitExport" in key for key in transformed)
    schedule = transformed["Schedule:Constant"]["Heating Setpoint"]
    assert schedule["hourly_value"] == 20.0


def test_output_meter_validation() -> None:
    config = BuildConfig(
        outputs=(
            OutputMapping(
                name="electricity",
                kind=OutputKind.METER,
                meter="Electricity:Facility",
            ),
        )
    )
    assert config.outputs[0].meter == "Electricity:Facility"

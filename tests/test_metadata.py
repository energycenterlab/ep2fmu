from __future__ import annotations

from xml.etree import ElementTree as ET

from ep2fmu.config import extract_legacy_config
from ep2fmu.metadata import (
    build_model_description,
    content_guid,
    sanitize_identifier,
    simulation_timing,
)
from ep2fmu.models import BuildConfig, OutputKind, OutputMapping


def test_identifier_is_fmi_safe() -> None:
    assert sanitize_identifier("123 My building!") == "m_123_My_building"


def test_simulation_timing(sample_epjson: dict[str, object]) -> None:
    assert simulation_timing(sample_epjson) == (900.0, 172800.0)


def test_guid_is_deterministic(sample_epjson: dict[str, object]) -> None:
    config = extract_legacy_config(sample_epjson)
    assert content_guid(sample_epjson, config, "1.0.0") == content_guid(
        sample_epjson, config, "1.0.0"
    )


def test_model_description_has_valid_initial_unknowns(
        sample_epjson: dict[str, object],
) -> None:
    config = extract_legacy_config(sample_epjson)
    xml = build_model_description(
        model_name="Building",
        model_identifier="Building",
        guid="{00000000-0000-0000-0000-000000000000}",
        config=config,
        stop_time=172800.0,
        generation_tool="ep2fmu test",
    )
    root = ET.fromstring(xml)
    co_sim = root.find("CoSimulation")
    assert co_sim is not None
    assert co_sim.attrib["needsExecutionTool"] == "true"
    outputs = root.findall("./ModelStructure/Outputs/Unknown")
    initial = root.findall("./ModelStructure/InitialUnknowns/Unknown")
    assert [item.attrib["index"] for item in outputs] == [item.attrib["index"] for item in initial]


def test_model_description_declares_configured_units() -> None:
    config = BuildConfig(
        outputs=(
            OutputMapping(
                name="temperature",
                kind=OutputKind.VARIABLE,
                key="Zone 1",
                variable="Zone Mean Air Temperature",
                unit="degC",
            ),
        )
    )
    xml = build_model_description(
        model_name="Building",
        model_identifier="Building",
        guid="{00000000-0000-0000-0000-000000000000}",
        config=config,
        stop_time=86400,
        generation_tool="ep2fmu test",
    )
    root = ET.fromstring(xml)
    assert root.find("./UnitDefinitions/Unit").attrib["name"] == "degC"  # type: ignore[union-attr]
    assert root.find("./ModelVariables/ScalarVariable/Real").attrib["unit"] == "degC"  # type: ignore[union-attr]

"""Deterministic model identity, time metadata, and FMI XML generation."""

from __future__ import annotations

import hashlib
import json
import re
from calendar import monthrange
from datetime import date
from typing import Any
from xml.etree import ElementTree as ET

from ep2fmu.constants import SUPPORTED_ENERGYPLUS_MODEL_VERSION
from ep2fmu.models import BuildConfig

IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_]")


def sanitize_identifier(value: str) -> str:
    identifier = IDENTIFIER_RE.sub("_", value.strip())
    identifier = re.sub(r"_+", "_", identifier).strip("_")
    if not identifier:
        identifier = "EnergyPlusModel"
    if identifier[0].isdigit():
        identifier = f"m_{identifier}"
    return identifier


def model_version(epjson: dict[str, Any]) -> str | None:
    raw = epjson.get("Version")
    if not isinstance(raw, dict):
        return None
    for value in raw.values():
        if isinstance(value, dict) and value.get("version_identifier") is not None:
            return str(value["version_identifier"])
    return None


def simulation_timing(epjson: dict[str, Any]) -> tuple[float, float]:
    """Return zone step and run-period duration in seconds."""

    steps_per_hour = 6
    timestep = epjson.get("Timestep")
    if isinstance(timestep, dict):
        for value in timestep.values():
            if isinstance(value, dict):
                candidate = value.get("number_of_timesteps_per_hour")
                if candidate is not None:
                    steps_per_hour = int(candidate)
                    break
    if steps_per_hour <= 0 or 3600 % steps_per_hour:
        raise ValueError("Timestep must divide 3600 seconds exactly")

    duration_days = 365
    run_period = epjson.get("RunPeriod")
    if isinstance(run_period, dict) and run_period:
        value = next(iter(run_period.values()))
        if isinstance(value, dict):
            begin_month = int(value.get("begin_month", 1))
            begin_day = int(value.get("begin_day_of_month", 1))
            end_month = int(value.get("end_month", 12))
            end_day = int(value.get("end_day_of_month", monthrange(2001, end_month)[1]))
            begin = date(2001, begin_month, begin_day)
            end = date(2001, end_month, end_day)
            if end < begin:
                end = date(2002, end_month, end_day)
            duration_days = (end - begin).days + 1
    return 3600.0 / steps_per_hour, float(duration_days * 86400)


def canonical_json(data: object) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_guid(
        epjson: dict[str, Any],
        config: BuildConfig,
        tool_version: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(canonical_json(epjson))
    digest.update(canonical_json(config.model_dump(mode="json")))
    digest.update(tool_version.encode("ascii"))
    value = digest.hexdigest()[:32]
    return f"{{{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}}}"


def build_model_description(
        *,
        model_name: str,
        model_identifier: str,
        guid: str,
        config: BuildConfig,
        stop_time: float,
        generation_tool: str,
) -> bytes:
    root = ET.Element(
        "fmiModelDescription",
        {
            "fmiVersion": "2.0",
            "modelName": model_name,
            "guid": guid,
            "description": (
                f"EnergyPlus {SUPPORTED_ENERGYPLUS_MODEL_VERSION} model "
                f"exported by {generation_tool}"
            ),
            "generationTool": generation_tool,
            "variableNamingConvention": "flat",
        },
    )
    ET.SubElement(
        root,
        "CoSimulation",
        {
            "modelIdentifier": model_identifier,
            "needsExecutionTool": "true",
            "canHandleVariableCommunicationStepSize": "true",
            "canInterpolateInputs": "false",
            "maxOutputDerivativeOrder": "0",
            "canGetAndSetFMUstate": "false",
            "canSerializeFMUstate": "false",
            "canRunAsynchronuously": "false",
            "canBeInstantiatedOnlyOncePerProcess": "false",
            "canNotUseMemoryManagementFunctions": "false",
            "providesDirectionalDerivative": "false",
        },
    )
    units = sorted(
        {mapping.unit for mapping in config.inputs if mapping.unit is not None}
        | {mapping.unit for mapping in config.outputs if mapping.unit is not None}
    )
    if units:
        definitions = ET.SubElement(root, "UnitDefinitions")
        for unit in units:
            ET.SubElement(definitions, "Unit", {"name": unit})
    ET.SubElement(root, "DefaultExperiment", {"startTime": "0", "stopTime": f"{stop_time:g}"})
    variables = ET.SubElement(root, "ModelVariables")

    parameter_specs = (
        ("energyplusHome", "1", "String", ""),
        ("outputDirectory", "2", "String", ""),
        ("keepOutputs", "3", "Boolean", "false"),
        ("runReadVars", "4", "Boolean", "false"),
    )
    for name, reference, scalar_type, start in parameter_specs:
        scalar = ET.SubElement(
            variables,
            "ScalarVariable",
            {
                "name": name,
                "valueReference": reference,
                "causality": "parameter",
                "variability": "fixed",
                "initial": "exact",
            },
        )
        ET.SubElement(scalar, scalar_type, {"start": start})

    sorted_inputs = sorted(config.inputs, key=lambda item: item.name)
    sorted_outputs = sorted(config.outputs, key=lambda item: item.name)
    input_indices: list[str] = []
    output_indices: list[str] = []
    next_index = len(parameter_specs) + 1
    for offset, input_mapping in enumerate(sorted_inputs):
        scalar = ET.SubElement(
            variables,
            "ScalarVariable",
            {
                "name": input_mapping.name,
                "valueReference": str(1000 + offset),
                "description": (f"EnergyPlus {input_mapping.kind.value}: {input_mapping.key}"),
                "causality": "input",
                "variability": "continuous",
            },
        )
        real_attributes = {"start": f"{input_mapping.start:.17g}"}
        if input_mapping.unit is not None:
            real_attributes["unit"] = input_mapping.unit
        ET.SubElement(scalar, "Real", real_attributes)
        input_indices.append(str(next_index))
        next_index += 1

    for offset, output_mapping in enumerate(sorted_outputs):
        description = output_mapping.variable if output_mapping.variable else output_mapping.meter
        scalar = ET.SubElement(
            variables,
            "ScalarVariable",
            {
                "name": output_mapping.name,
                "valueReference": str(100000 + offset),
                "description": f"EnergyPlus {output_mapping.kind.value}: {description}",
                "causality": "output",
                "variability": "continuous",
                "initial": "calculated",
            },
        )
        real_attributes = {}
        if output_mapping.unit is not None:
            real_attributes["unit"] = output_mapping.unit
        ET.SubElement(scalar, "Real", real_attributes)
        output_indices.append(str(next_index))
        next_index += 1

    structure = ET.SubElement(root, "ModelStructure")
    outputs = ET.SubElement(structure, "Outputs")
    initial_unknowns = ET.SubElement(structure, "InitialUnknowns")
    dependencies = " ".join(input_indices)
    for index in output_indices:
        attributes = {"index": index}
        if dependencies:
            attributes["dependencies"] = dependencies
            attributes["dependenciesKind"] = "dependent " * (len(input_indices) - 1) + "dependent"
        ET.SubElement(outputs, "Unknown", attributes)
        ET.SubElement(initial_unknowns, "Unknown", {"index": index})

    ET.indent(root, space="  ")
    result: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return result

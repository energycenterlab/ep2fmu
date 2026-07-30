"""Configuration loading, legacy mapping extraction, and epJSON transformation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ep2fmu.errors import InvalidInputError, MappingError
from ep2fmu.models import (
    BuildConfig,
    InputKind,
    InputMapping,
    ModelMetadata,
    OutputKind,
    OutputMapping,
)

LEGACY_FROM_VARIABLE = "ExternalInterface:FunctionalMockupUnitExport:From:Variable"
LEGACY_TO_ACTUATOR = "ExternalInterface:FunctionalMockupUnitExport:To:Actuator"
LEGACY_TO_SCHEDULE = "ExternalInterface:FunctionalMockupUnitExport:To:Schedule"
LEGACY_TO_VARIABLE = "ExternalInterface:FunctionalMockupUnitExport:To:Variable"
LEGACY_CONTAINERS = (
    LEGACY_FROM_VARIABLE,
    LEGACY_TO_ACTUATOR,
    LEGACY_TO_SCHEDULE,
    LEGACY_TO_VARIABLE,
)


def _field(data: Mapping[str, Any], *names: str, required: bool = True) -> Any:
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    if required:
        raise MappingError(f"missing epJSON field; expected one of: {', '.join(names)}")
    return None


def _records(epjson: Mapping[str, Any], object_type: str) -> list[tuple[str, Mapping[str, Any]]]:
    raw = epjson.get(object_type, {})
    if not isinstance(raw, Mapping):
        raise MappingError(f"{object_type} must be an epJSON object")
    records: list[tuple[str, Mapping[str, Any]]] = []
    for object_name, fields in raw.items():
        if not isinstance(fields, Mapping):
            raise MappingError(f"{object_type}/{object_name} must be an epJSON object")
        records.append((str(object_name), fields))
    return records


def _external_interface_name(fields: Mapping[str, Any]) -> str:
    value = _field(
        fields,
        "name_of_external_interface",
        "name",
        "external_interface_name",
        required=False,
    )
    return str(value or "")


def extract_legacy_config(epjson: Mapping[str, Any]) -> BuildConfig:
    """Translate EnergyPlus FMU-export objects into the public config model."""

    inputs: list[InputMapping] = []
    outputs: list[OutputMapping] = []

    for object_name, fields in _records(epjson, LEGACY_FROM_VARIABLE):
        outputs.append(
            OutputMapping(
                name=str(
                    _field(fields, "fmu_variable_name", "fmu_variable", required=False)
                    or object_name
                ),
                kind=OutputKind.VARIABLE,
                key=str(
                    _field(
                        fields,
                        "output_variable_index_key_name",
                        "energyplus_key_value",
                        "key_value",
                    )
                ),
                variable=str(_field(fields, "output_variable_name", "energyplus_variable_name")),
            )
        )

    for object_name, fields in _records(epjson, LEGACY_TO_ACTUATOR):
        alias = str(
            _field(fields, "energyplus_variable_name", "erl_variable_name", required=False)
            or object_name
        )
        inputs.append(
            InputMapping(
                name=str(_field(fields, "fmu_variable_name", "fmu_variable")),
                kind=InputKind.ACTUATOR,
                key=str(
                    _field(
                        fields,
                        "actuated_component_unique_name",
                        "actuated_component_unique_key_name",
                    )
                ),
                component_type=str(_field(fields, "actuated_component_type")),
                control_type=str(_field(fields, "actuated_component_control_type")),
                start=float(_field(fields, "initial_value", required=False) or 0.0),
                legacy_alias=alias,
            )
        )

    for object_name, fields in _records(epjson, LEGACY_TO_SCHEDULE):
        inputs.append(
            InputMapping(
                name=str(_field(fields, "fmu_variable_name", "fmu_variable")),
                kind=InputKind.SCHEDULE,
                key=str(_field(fields, "schedule_name", required=False) or object_name),
                schedule_type_limits=str(
                    _field(
                        fields,
                        "schedule_type_limits_names",
                        "schedule_type_limits_name",
                    )
                ),
                start=float(_field(fields, "initial_value", required=False) or 0.0),
            )
        )

    for object_name, fields in _records(epjson, LEGACY_TO_VARIABLE):
        inputs.append(
            InputMapping(
                name=str(_field(fields, "fmu_variable_name", "fmu_variable")),
                kind=InputKind.EMS_GLOBAL,
                key=str(_field(fields, "energyplus_variable_name", required=False) or object_name),
                start=float(_field(fields, "initial_value", required=False) or 0.0),
            )
        )

    return BuildConfig(model=ModelMetadata(), inputs=tuple(inputs), outputs=tuple(outputs))


def load_yaml_config(path: Path) -> BuildConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvalidInputError(f"cannot read config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise InvalidInputError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise InvalidInputError(f"config {path} must contain a YAML mapping")
    try:
        return BuildConfig.model_validate(raw)
    except ValidationError as exc:
        raise InvalidInputError(f"invalid config {path}:\n{exc}") from exc


def merge_configs(legacy: BuildConfig, overlay: BuildConfig | None) -> BuildConfig:
    """Merge by FMU variable name, with YAML taking precedence."""

    if overlay is None:
        return legacy
    inputs = {mapping.name: mapping for mapping in legacy.inputs}
    outputs = {mapping.name: mapping for mapping in legacy.outputs}
    for input_mapping in overlay.inputs:
        outputs.pop(input_mapping.name, None)
        inputs[input_mapping.name] = input_mapping
    for output_mapping in overlay.outputs:
        inputs.pop(output_mapping.name, None)
        outputs[output_mapping.name] = output_mapping
    return BuildConfig(
        schema_version=overlay.schema_version,
        model=overlay.model,
        inputs=tuple(inputs[name] for name in sorted(inputs)),
        outputs=tuple(outputs[name] for name in sorted(outputs)),
    )


def load_epjson(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvalidInputError(f"cannot read model {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"invalid epJSON model {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInputError(f"epJSON model {path} must contain a JSON object")
    return data


def transform_epjson(epjson: Mapping[str, Any], config: BuildConfig) -> dict[str, Any]:
    """Create the runtime model without the legacy ExternalInterface transport."""

    transformed: dict[str, Any] = deepcopy(dict(epjson))
    for key in LEGACY_CONTAINERS:
        transformed.pop(key, None)

    external = transformed.get("ExternalInterface")
    if isinstance(external, dict):
        filtered = {
            name: value
            for name, value in external.items()
            if str(name).casefold() != "functionalmockupunitexport"
               and not (
                    isinstance(value, Mapping)
                    and _external_interface_name(value).casefold() == "functionalmockupunitexport"
            )
        }
        if filtered:
            transformed["ExternalInterface"] = filtered
        else:
            transformed.pop("ExternalInterface", None)

    schedules = transformed.setdefault("Schedule:Constant", {})
    ems_globals = transformed.setdefault("EnergyManagementSystem:GlobalVariable", {})
    if not isinstance(schedules, dict) or not isinstance(ems_globals, dict):
        raise MappingError("Schedule:Constant and EMS global containers must be objects")

    for mapping in config.inputs:
        if mapping.kind == InputKind.SCHEDULE:
            schedules[mapping.key] = {
                "schedule_type_limits_name": mapping.schedule_type_limits,
                "hourly_value": mapping.start,
            }
        elif mapping.kind == InputKind.EMS_GLOBAL:
            ems_globals.setdefault(mapping.key, {})
        elif mapping.legacy_alias:
            ems_globals.setdefault(mapping.legacy_alias, {})

    if not schedules:
        transformed.pop("Schedule:Constant", None)
    if not ems_globals:
        transformed.pop("EnergyManagementSystem:GlobalVariable", None)
    return transformed

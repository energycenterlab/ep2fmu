"""Stable Python build and validation API."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from ep2fmu import __version__
from ep2fmu.config import (
    extract_legacy_config,
    load_epjson,
    load_yaml_config,
    merge_configs,
    transform_epjson,
)
from ep2fmu.constants import (
    SUPPORTED_ENERGYPLUS_MODEL_VERSION,
    SUPPORTED_ENERGYPLUS_VERSION,
)
from ep2fmu.energyplus import convert_model, resolve_energyplus
from ep2fmu.errors import Ep2FmuError, InvalidInputError
from ep2fmu.metadata import (
    build_model_description,
    content_guid,
    model_version,
    sanitize_identifier,
    simulation_timing,
)
from ep2fmu.models import (
    BuildOptions,
    BuildResult,
    ValidationIssue,
    ValidationReport,
)
from ep2fmu.packaging import runtime_config, write_fmu
from ep2fmu.resources import collect_model_resources
from ep2fmu.runtime_assets import load_runtime_asset


def _sidecar_path(model_path: Path) -> Path:
    return model_path.with_suffix(".ep2fmu.yaml")


def _mapping_payload(config: Any) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    inputs = [
        mapping.model_dump(mode="json", exclude_none=True, exclude={"legacy_alias"})
        for mapping in sorted(config.inputs, key=lambda value: value.name)
    ]
    outputs = [
        mapping.model_dump(mode="json", exclude_none=True)
        for mapping in sorted(config.outputs, key=lambda value: value.name)
    ]
    return inputs, outputs


def _prepare(options: BuildOptions, workdir: Path) -> tuple[Any, ...]:
    model_source = options.model_path.expanduser().resolve()
    if options.weather_path is not None and not options.weather_path.expanduser().is_file():
        raise InvalidInputError(f"weather file does not exist: {options.weather_path}")
    installation = resolve_energyplus(options.energyplus_home)
    converted_path = convert_model(model_source, installation, workdir)
    epjson = load_epjson(converted_path)
    model_resources = collect_model_resources(model_source, epjson)
    version = model_version(epjson)
    if version not in {
        SUPPORTED_ENERGYPLUS_MODEL_VERSION,
        SUPPORTED_ENERGYPLUS_VERSION,
    }:
        raise InvalidInputError(
            f"model must target EnergyPlus {SUPPORTED_ENERGYPLUS_MODEL_VERSION}; "
            f"found {version or 'no Version object'}"
        )
    legacy = extract_legacy_config(epjson)
    config_path = options.config_path
    if config_path is None:
        candidate = _sidecar_path(model_source)
        config_path = candidate if candidate.is_file() else None
    overlay = load_yaml_config(config_path) if config_path is not None else None
    config = merge_configs(legacy, overlay)
    if not config.inputs and not config.outputs:
        raise InvalidInputError(
            "no FMU variables found; add legacy export objects or an ep2fmu YAML config"
        )
    transformed = transform_epjson(epjson, config)
    zone_step, stop_time = simulation_timing(transformed)
    model_name = config.model.name or model_source.stem
    model_identifier = sanitize_identifier(model_name)
    guid = content_guid(transformed, config, __version__)
    return (
        installation,
        transformed,
        config,
        zone_step,
        stop_time,
        model_name,
        model_identifier,
        guid,
        model_resources,
    )


def validate_model(options: BuildOptions) -> ValidationReport:
    """Validate inputs and mappings without creating an FMU."""

    try:
        with tempfile.TemporaryDirectory(prefix="ep2fmu-validate-") as temporary:
            prepared = _prepare(options, Path(temporary))
        installation, _model, config, _step, _stop, _name, identifier, _guid, _resources = prepared
        return ValidationReport(
            valid=True,
            model_identifier=identifier,
            energyplus_version=installation.version,
            inputs=len(config.inputs),
            outputs=len(config.outputs),
        )
    except Ep2FmuError as exc:
        return ValidationReport(
            valid=False,
            issues=(
                ValidationIssue(
                    severity="error",
                    code=exc.__class__.__name__,
                    message=str(exc),
                ),
            ),
        )
    except ValueError as exc:
        return ValidationReport(
            valid=False,
            issues=(ValidationIssue(severity="error", code="InvalidModel", message=str(exc)),),
        )


def build_fmu(options: BuildOptions) -> BuildResult:
    """Build a deterministic FMI 2.0 Co-Simulation archive."""

    with tempfile.TemporaryDirectory(prefix="ep2fmu-build-") as temporary:
        prepared = _prepare(options, Path(temporary))
        (
            _installation,
            transformed,
            config,
            zone_step,
            stop_time,
            model_name,
            identifier,
            guid,
            model_resources,
        ) = prepared
        inputs, outputs = _mapping_payload(config)
        weather_path = options.weather_path.expanduser().resolve() if options.weather_path else None
        weather_name = weather_path.name if weather_path else None
        config_json = runtime_config(
            model_file="model.epJSON",
            weather_file=weather_name,
            zone_step_seconds=zone_step,
            stop_time_seconds=stop_time,
            inputs=inputs,
            outputs=outputs,
        )
        description = build_model_description(
            model_name=model_name,
            model_identifier=identifier,
            guid=guid,
            config=config,
            stop_time=stop_time,
            generation_tool=f"ep2fmu {__version__}",
        )
        assets = tuple(load_runtime_asset(platform) for platform in options.platforms)
        output_path = (
            options.output_path.expanduser().resolve()
            if options.output_path
            else options.model_path.with_name(f"{identifier}.fmu").resolve()
        )
        write_fmu(
            output_path,
            model_identifier=identifier,
            model_description=description,
            model_epjson=json.dumps(
                transformed,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
                         + b"\n",
            config_json=config_json,
            weather_name=weather_name,
            weather_data=weather_path.read_bytes() if weather_path else None,
            runtime_assets=assets,
            model_resources=model_resources,
        )
        return BuildResult(
            fmu_path=output_path,
            model_identifier=identifier,
            guid=guid,
            platforms=options.platforms,
            input_count=len(config.inputs),
            output_count=len(config.outputs),
        )

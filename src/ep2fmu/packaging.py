"""Deterministic FMU archive assembly."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ep2fmu.constants import (
    FMU_BINARY_SUFFIX,
    NORMALIZED_ZIP_TIMESTAMP,
    SUPPORTED_ENERGYPLUS_VERSION,
    Platform,
    fmi_platform_directory,
)
from ep2fmu.errors import PackagingError
from ep2fmu.resources import ModelResource
from ep2fmu.runtime_assets import RuntimeAsset


def runtime_config(
        *,
        model_file: str,
        weather_file: str | None,
        zone_step_seconds: float,
        stop_time_seconds: float,
        inputs: list[dict[str, object]],
        outputs: list[dict[str, object]],
) -> bytes:
    payload = {
        "schema_version": 1,
        "energyplus_version": SUPPORTED_ENERGYPLUS_VERSION,
        "model": model_file,
        "weather": weather_file,
        "zone_step_seconds": zone_step_seconds,
        "stop_time_seconds": stop_time_seconds,
        "inputs": inputs,
        "outputs": outputs,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _zip_info(path: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, NORMALIZED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o100755 if executable else 0o100644
    info.external_attr = mode << 16
    return info


def write_fmu(
        output_path: Path,
        *,
        model_identifier: str,
        model_description: bytes,
        model_epjson: bytes,
        config_json: bytes,
        weather_name: str | None,
        weather_data: bytes | None,
        runtime_assets: tuple[RuntimeAsset, ...],
        model_resources: tuple[ModelResource, ...] = (),
) -> None:
    entries: dict[str, tuple[bytes, bool]] = {
        "modelDescription.xml": (model_description, False),
        "resources/model.epJSON": (model_epjson, False),
        "resources/ep2fmu-config.json": (config_json, False),
    }
    if weather_name is not None and weather_data is not None:
        entries[f"resources/{weather_name}"] = (weather_data, False)
    for resource in model_resources:
        path = f"resources/{resource.name}"
        if path in entries:
            raise PackagingError(f"model resource conflicts with reserved FMU path: {path}")
        try:
            entries[path] = (resource.source.read_bytes(), False)
        except OSError as exc:
            raise PackagingError(f"cannot read model resource {resource.source}: {exc}") from exc
    for asset in runtime_assets:
        platform = asset.platform
        platform_directory = fmi_platform_directory(platform)
        library_name = f"{model_identifier}{FMU_BINARY_SUFFIX[platform]}"
        worker_name = "ep2fmu-worker.exe" if platform == Platform.WIN64 else "ep2fmu-worker"
        runtime_entries = {
            f"binaries/{platform_directory}/{library_name}": (asset.library, True),
            f"resources/bin/{platform_directory}/{worker_name}": (asset.worker, True),
        }
        for path, value in runtime_entries.items():
            previous = entries.get(path)
            if previous is not None and previous != value:
                raise PackagingError(
                    "macOS x64 and arm64 runtime bundles must contain the same universal2 binaries"
                )
            entries[path] = value

    output_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", allowZip64=True) as archive:
            for path in sorted(entries):
                data, executable = entries[path]
                archive.writestr(_zip_info(path, executable=executable), data)
        output_path.write_bytes(buffer.getvalue())
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackagingError(f"cannot write FMU {output_path}: {exc}") from exc

"""Resolve prebuilt Rust runtime bundles shipped as opaque package data."""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ep2fmu.constants import FMU_BINARY_SUFFIX, Platform
from ep2fmu.errors import PackagingError


@dataclass(frozen=True, slots=True)
class RuntimeAsset:
    platform: Platform
    library: bytes
    worker: bytes


def _read_directory(root: Path, platform: Platform) -> RuntimeAsset:
    platform_dir = root / platform.value
    library_path = platform_dir / f"ep2fmu_fmi2{FMU_BINARY_SUFFIX[platform]}"
    worker_name = "ep2fmu-worker.exe" if platform == Platform.WIN64 else "ep2fmu-worker"
    worker_path = platform_dir / worker_name
    try:
        return RuntimeAsset(
            platform=platform,
            library=library_path.read_bytes(),
            worker=worker_path.read_bytes(),
        )
    except OSError as exc:
        raise PackagingError(
            f"missing prebuilt runtime for {platform.value} under {platform_dir}"
        ) from exc


def load_runtime_asset(platform: Platform) -> RuntimeAsset:
    """Load an override directory or a packaged runtime zip."""

    if override := os.environ.get("EP2FMU_RUNTIME_DIR"):
        return _read_directory(Path(override), platform)

    archive = resources.files("ep2fmu").joinpath("runtime", f"{platform.value}.zip")
    try:
        with archive.open("rb") as stream, zipfile.ZipFile(stream) as bundle:
            library_name = f"ep2fmu_fmi2{FMU_BINARY_SUFFIX[platform]}"
            worker_name = "ep2fmu-worker.exe" if platform == Platform.WIN64 else "ep2fmu-worker"
            return RuntimeAsset(
                platform=platform,
                library=bundle.read(library_name),
                worker=bundle.read(worker_name),
            )
    except (FileNotFoundError, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise PackagingError(
            f"prebuilt runtime bundle is unavailable for {platform.value}; "
            "install an official wheel or set EP2FMU_RUNTIME_DIR"
        ) from exc

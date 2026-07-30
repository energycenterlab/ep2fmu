"""EnergyPlus 26.1 discovery and IDF conversion."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ep2fmu.constants import SUPPORTED_ENERGYPLUS_VERSION
from ep2fmu.errors import (
    EnergyPlusNotFoundError,
    EnergyPlusVersionError,
    InvalidInputError,
)

VERSION_PATTERN = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")


@dataclass(frozen=True, slots=True)
class EnergyPlusInstallation:
    home: Path
    executable: Path
    library: Path | None
    version: str


def _executable_in_home(home: Path) -> Path | None:
    candidates = (home / "energyplus", home / "energyplus.exe")
    return next((path for path in candidates if path.is_file()), None)


def _library_in_home(home: Path) -> Path | None:
    candidates = (
        home / "libenergyplusapi.so",
        home / "libenergyplusapi.dylib",
        home / "energyplusapi.dll",
        home / "EnergyPlusAPI.dll",
    )
    return next((path for path in candidates if path.is_file()), None)


def _read_version(executable: Path) -> str:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnergyPlusNotFoundError(f"cannot execute {executable}: {exc}") from exc
    output = f"{result.stdout}\n{result.stderr}"
    match = VERSION_PATTERN.search(output)
    if not match:
        raise EnergyPlusVersionError(
            f"could not determine EnergyPlus version from {executable}: {output.strip()}"
        )
    return match.group(1)


def resolve_energyplus(explicit_home: Path | None = None) -> EnergyPlusInstallation:
    """Resolve CLI option, ENERGYPLUS_HOME, then PATH."""

    executable: Path | None = None
    home: Path | None = None
    if explicit_home is not None:
        home = explicit_home.expanduser().resolve()
        executable = _executable_in_home(home)
    elif os.environ.get("ENERGYPLUS_HOME"):
        home = Path(os.environ["ENERGYPLUS_HOME"]).expanduser().resolve()
        executable = _executable_in_home(home)
    else:
        resolved = shutil.which("energyplus")
        if resolved:
            executable = Path(resolved).resolve()
            home = executable.parent

    if executable is None or home is None:
        source = explicit_home or os.environ.get("ENERGYPLUS_HOME") or "PATH"
        raise EnergyPlusNotFoundError(f"EnergyPlus executable not found using {source}")
    version = _read_version(executable)
    if version != SUPPORTED_ENERGYPLUS_VERSION:
        raise EnergyPlusVersionError(
            f"EnergyPlus {SUPPORTED_ENERGYPLUS_VERSION} is required; "
            f"found {version} at {executable}"
        )
    return EnergyPlusInstallation(
        home=home,
        executable=executable,
        library=_library_in_home(home),
        version=version,
    )


def convert_model(model_path: Path, installation: EnergyPlusInstallation, workdir: Path) -> Path:
    """Return an epJSON model in workdir without touching the source model."""

    source = model_path.expanduser().resolve()
    if not source.is_file():
        raise InvalidInputError(f"model does not exist: {source}")
    suffix = source.suffix.casefold()
    if suffix == ".epjson":
        destination = workdir / source.name
        shutil.copy2(source, destination)
        return destination
    if suffix != ".idf":
        raise InvalidInputError("model must use the .idf or .epJSON extension")

    copied = workdir / source.name
    shutil.copy2(source, copied)
    result = subprocess.run(
        [str(installation.executable), "--convert-only", copied.name],
        cwd=workdir,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise InvalidInputError(f"EnergyPlus IDF conversion failed: {detail}")
    candidates = sorted(workdir.glob("*.epJSON")) + sorted(workdir.glob("*.epjson"))
    if not candidates:
        raise InvalidInputError("EnergyPlus conversion completed without producing an epJSON file")
    return candidates[0]

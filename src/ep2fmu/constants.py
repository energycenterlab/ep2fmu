"""Project-wide constants."""

from __future__ import annotations

import os
from enum import StrEnum

DEFAULT_ENERGYPLUS_VERSION = "26.1.0"
SUPPORTED_ENERGYPLUS_VERSION = os.environ.get(
    "EP2FMU_ENERGYPLUS_VERSION", DEFAULT_ENERGYPLUS_VERSION
)
SUPPORTED_ENERGYPLUS_MODEL_VERSION = ".".join(SUPPORTED_ENERGYPLUS_VERSION.split(".")[:2])
CONFIG_SCHEMA_VERSION = 1
FMI_VERSION = "2.0"
NORMALIZED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class Platform(StrEnum):
    """FMU platform identifiers used by ep2fmu."""

    LINUX64 = "linux64"
    WIN64 = "win64"
    DARWIN64 = "darwin64"
    DARWINARM64 = "darwinarm64"


ALL_PLATFORMS = tuple(Platform)

FMU_BINARY_SUFFIX: dict[Platform, str] = {
    Platform.LINUX64: ".so",
    Platform.WIN64: ".dll",
    Platform.DARWIN64: ".dylib",
    Platform.DARWINARM64: ".dylib",
}


def fmi_platform_directory(platform: Platform) -> str:
    """Return the FMI 2 platform tuple used inside an FMU.

    FMI 2 has only the ``darwin64`` tuple for 64-bit macOS.  Official ep2fmu
    releases therefore ship a universal2 library and worker for both macOS
    selectors.
    """

    if platform in (Platform.DARWIN64, Platform.DARWINARM64):
        return Platform.DARWIN64.value
    return platform.value

"""Typed exceptions and CLI exit codes."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    INVALID_INPUT = 2
    ENERGYPLUS_NOT_FOUND = 3
    ENERGYPLUS_INCOMPATIBLE = 4
    MAPPING_UNRESOLVED = 5
    PACKAGING_FAILED = 6


class Ep2FmuError(Exception):
    """Base user-facing error."""

    exit_code = ExitCode.INVALID_INPUT


class InvalidInputError(Ep2FmuError):
    """The model or configuration is invalid."""


class EnergyPlusNotFoundError(Ep2FmuError):
    """EnergyPlus could not be resolved."""

    exit_code = ExitCode.ENERGYPLUS_NOT_FOUND


class EnergyPlusVersionError(Ep2FmuError):
    """An unsupported EnergyPlus version was resolved."""

    exit_code = ExitCode.ENERGYPLUS_INCOMPATIBLE


class MappingError(Ep2FmuError):
    """An EnergyPlus/FMI mapping is invalid or cannot be resolved."""

    exit_code = ExitCode.MAPPING_UNRESOLVED


class PackagingError(Ep2FmuError):
    """The FMU could not be assembled."""

    exit_code = ExitCode.PACKAGING_FAILED

"""Versioned public configuration and result models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ep2fmu.constants import ALL_PLATFORMS, Platform


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputKind(StrEnum):
    ACTUATOR = "actuator"
    SCHEDULE = "schedule"
    EMS_GLOBAL = "ems_global"


class OutputKind(StrEnum):
    VARIABLE = "variable"
    METER = "meter"


class ModelMetadata(StrictModel):
    name: str | None = None


class InputMapping(StrictModel):
    name: str = Field(min_length=1)
    kind: InputKind
    key: str = Field(min_length=1)
    start: float = 0.0
    unit: str | None = None
    component_type: str | None = None
    control_type: str | None = None
    schedule_type_limits: str | None = None
    legacy_alias: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> InputMapping:
        if self.kind == InputKind.ACTUATOR:
            if not self.component_type or not self.control_type:
                raise ValueError("actuator inputs require component_type and control_type")
        elif self.component_type or self.control_type:
            raise ValueError("component_type/control_type are only valid for actuator inputs")
        if self.kind == InputKind.SCHEDULE and not self.schedule_type_limits:
            raise ValueError("schedule inputs require schedule_type_limits")
        if self.kind != InputKind.SCHEDULE and self.schedule_type_limits:
            raise ValueError("schedule_type_limits is only valid for schedule inputs")
        return self


class OutputMapping(StrictModel):
    name: str = Field(min_length=1)
    kind: OutputKind
    key: str | None = None
    variable: str | None = None
    meter: str | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> OutputMapping:
        if self.kind == OutputKind.VARIABLE:
            if not self.key or not self.variable:
                raise ValueError("variable outputs require key and variable")
            if self.meter:
                raise ValueError("meter is only valid for meter outputs")
        else:
            if not self.meter:
                raise ValueError("meter outputs require meter")
            if self.key or self.variable:
                raise ValueError("key/variable are only valid for variable outputs")
        return self


class BuildConfig(StrictModel):
    schema_version: Literal[1] = 1
    model: ModelMetadata = Field(default_factory=ModelMetadata)
    inputs: tuple[InputMapping, ...] = ()
    outputs: tuple[OutputMapping, ...] = ()

    @model_validator(mode="after")
    def unique_fmu_names(self) -> BuildConfig:
        names = [item.name for item in self.inputs]
        names.extend(item.name for item in self.outputs)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate FMU variable names: {', '.join(duplicates)}")
        return self


class BuildOptions(StrictModel):
    model_path: Path
    weather_path: Path | None = None
    config_path: Path | None = None
    energyplus_home: Path | None = None
    output_path: Path | None = None
    platforms: tuple[Platform, ...] = ALL_PLATFORMS

    @model_validator(mode="after")
    def normalize_platforms(self) -> BuildOptions:
        if not self.platforms:
            raise ValueError("at least one target platform is required")
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("target platforms must be unique")
        return self


class BuildResult(StrictModel):
    fmu_path: Path
    model_identifier: str
    guid: str
    platforms: tuple[Platform, ...]
    input_count: int
    output_count: int


class ValidationIssue(StrictModel):
    severity: Literal["warning", "error"]
    code: str
    message: str


class ValidationReport(StrictModel):
    valid: bool
    model_identifier: str | None = None
    energyplus_version: str | None = None
    inputs: int = 0
    outputs: int = 0
    issues: tuple[ValidationIssue, ...] = ()


PlatformList = Annotated[tuple[Platform, ...], Field(min_length=1)]

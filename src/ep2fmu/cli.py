"""Typer command-line interface."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from ep2fmu import __version__
from ep2fmu.api import build_fmu, validate_model
from ep2fmu.constants import ALL_PLATFORMS, SUPPORTED_ENERGYPLUS_MODEL_VERSION, Platform
from ep2fmu.energyplus import resolve_energyplus
from ep2fmu.errors import Ep2FmuError, ExitCode
from ep2fmu.inspection import inspect_fmu
from ep2fmu.models import BuildOptions

app = typer.Typer(
    name="ep2fmu",
    help=(
        f"Export EnergyPlus {SUPPORTED_ENERGYPLUS_MODEL_VERSION} models as "
        "compiler-free FMI 2.0 Co-Simulation FMUs."
    ),
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ep2fmu {__version__}")
        raise typer.Exit()


@app.callback()
def main(
        version: Annotated[
            bool | None,
            typer.Option("--version", callback=_version_callback, is_eager=True),
        ] = None,
) -> None:
    """Fast EnergyPlus-to-FMU export."""


def _platforms(values: list[str]) -> tuple[Platform, ...]:
    if not values or "all" in values:
        return ALL_PLATFORMS
    try:
        return tuple(Platform(value) for value in values)
    except ValueError as exc:
        choices = ", ".join(["all", *(platform.value for platform in ALL_PLATFORMS)])
        raise typer.BadParameter(f"platform must be one of: {choices}") from exc


def _default_platforms() -> list[str]:
    configured = os.environ.get("EP2FMU_DEFAULT_PLATFORM")
    return [configured] if configured else ["all"]


def _options(
        model: Path,
        weather: Path | None,
        config: Path | None,
        energyplus_home: Path | None,
        output: Path | None,
        platform: list[str],
) -> BuildOptions:
    try:
        return BuildOptions(
            model_path=model,
            weather_path=weather,
            config_path=config,
            energyplus_home=energyplus_home,
            output_path=output,
            platforms=_platforms(platform),
        )
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("build")
def build_command(
        model: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        weather: Annotated[Path, typer.Option("--weather", "-w", exists=True, dir_okay=False)],
        config: Annotated[
            Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
        ] = None,
        energyplus_home: Annotated[
            Path | None, typer.Option("--energyplus-home", envvar="ENERGYPLUS_HOME")
        ] = None,
        output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
        platform: Annotated[
            list[str] | None,
            typer.Option("--platform", "-p", help="all or one/more FMU platform identifiers"),
        ] = None,
) -> None:
    """Build a deterministic FMU."""

    try:
        result = build_fmu(
            _options(
                model, weather, config, energyplus_home, output, platform or _default_platforms()
            )
        )
    except Ep2FmuError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(int(exc.exit_code)) from exc
    typer.echo(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True)
    )


@app.command("validate")
def validate_command(
        model: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        weather: Annotated[
            Path | None, typer.Option("--weather", "-w", exists=True, dir_okay=False)
        ] = None,
        config: Annotated[
            Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
        ] = None,
        energyplus_home: Annotated[
            Path | None, typer.Option("--energyplus-home", envvar="ENERGYPLUS_HOME")
        ] = None,
) -> None:
    """Validate model, mappings, and EnergyPlus without creating an FMU."""

    report = validate_model(_options(model, weather, config, energyplus_home, None, ["all"]))
    typer.echo(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True)
    )
    if not report.valid:
        issue_code = report.issues[0].code if report.issues else ""
        exit_code = {
            "EnergyPlusNotFoundError": ExitCode.ENERGYPLUS_NOT_FOUND,
            "EnergyPlusVersionError": ExitCode.ENERGYPLUS_INCOMPATIBLE,
            "MappingError": ExitCode.MAPPING_UNRESOLVED,
            "PackagingError": ExitCode.PACKAGING_FAILED,
        }.get(issue_code, ExitCode.INVALID_INPUT)
        raise typer.Exit(int(exit_code))


@app.command("inspect")
def inspect_command(
        fmu: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Inspect an ep2fmu archive."""

    try:
        result = inspect_fmu(fmu)
    except Ep2FmuError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(int(exc.exit_code)) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


@app.command("doctor")
def doctor_command(
        energyplus_home: Annotated[
            Path | None, typer.Option("--energyplus-home", envvar="ENERGYPLUS_HOME")
        ] = None,
) -> None:
    """Check the supported EnergyPlus installation."""

    try:
        installation = resolve_energyplus(energyplus_home)
    except Ep2FmuError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(int(exc.exit_code)) from exc
    result = {
        "healthy": installation.library is not None,
        "home": str(installation.home),
        "executable": str(installation.executable),
        "library": str(installation.library) if installation.library else None,
        "version": installation.version,
    }
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if installation.library is None:
        raise typer.Exit(int(ExitCode.ENERGYPLUS_NOT_FOUND))


if __name__ == "__main__":
    app()

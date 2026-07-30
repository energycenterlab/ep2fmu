"""Read-only FMU inspection."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ep2fmu.errors import InvalidInputError


def inspect_fmu(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            root = ET.fromstring(archive.read("modelDescription.xml"))
            runtime = json.loads(archive.read("resources/ep2fmu-config.json"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, json.JSONDecodeError) as exc:
        raise InvalidInputError(f"invalid ep2fmu archive {path}: {exc}") from exc

    co_simulation = root.find("CoSimulation")
    platforms = sorted(
        {
            name.split("/")[1]
            for name in names
            if name.startswith("binaries/") and len(name.split("/")) >= 3
        }
    )
    variables = root.find("ModelVariables")
    scalar_variables = list(variables) if variables is not None else []
    return {
        "path": str(path.resolve()),
        "fmi_version": root.attrib.get("fmiVersion"),
        "model_name": root.attrib.get("modelName"),
        "model_identifier": (
            co_simulation.attrib.get("modelIdentifier") if co_simulation is not None else None
        ),
        "guid": root.attrib.get("guid"),
        "platforms": platforms,
        "variables": len(scalar_variables),
        "inputs": len(runtime.get("inputs", [])),
        "outputs": len(runtime.get("outputs", [])),
        "zone_step_seconds": runtime.get("zone_step_seconds"),
        "stop_time_seconds": runtime.get("stop_time_seconds"),
    }

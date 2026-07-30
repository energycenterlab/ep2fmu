"""Discovery and normalization of files referenced by an EnergyPlus model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ep2fmu.errors import InvalidInputError


@dataclass(frozen=True, slots=True)
class ModelResource:
    """A model dependency copied into the FMU resources directory."""

    source: Path
    name: str


def _referenced_file_fields(value: Any) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("file_name"), str):
            fields.append(value)
        for child in value.values():
            fields.extend(_referenced_file_fields(child))
    elif isinstance(value, list):
        for child in value:
            fields.extend(_referenced_file_fields(child))
    return fields


def collect_model_resources(
        model_path: Path,
        epjson: dict[str, Any],
) -> tuple[ModelResource, ...]:
    """Resolve epJSON ``file_name`` dependencies and make their paths portable.

    EnergyPlus retains paths from objects such as ``Construction:WindowDataFile``
    and ``Schedule:File`` during IDF conversion. Each dependency is resolved
    relative to the user's model, renamed to a safe FMU-local basename, and the
    in-memory epJSON is updated to reference that basename.
    """

    model_directory = model_path.expanduser().resolve().parent
    by_name: dict[str, Path] = {}

    for fields in _referenced_file_fields(epjson):
        raw_name = fields["file_name"].strip()
        if not raw_name:
            continue
        portable_path = raw_name.replace("\\", "/")
        referenced = Path(portable_path)
        source = (
            referenced.expanduser().resolve()
            if referenced.is_absolute()
            else (model_directory / referenced).resolve()
        )
        if not source.is_file():
            raise InvalidInputError(
                f"model resource does not exist: {raw_name} "
                f"(resolved relative to {model_directory})"
            )

        resource_name = source.name
        previous = by_name.get(resource_name)
        if previous is not None and previous != source:
            raise InvalidInputError(
                f"model resources {previous} and {source} have the same FMU name {resource_name!r}"
            )
        by_name[resource_name] = source
        fields["file_name"] = resource_name

    return tuple(
        ModelResource(source=source, name=name) for name, source in sorted(by_name.items())
    )

from __future__ import annotations

from pathlib import Path

import pytest

from ep2fmu.errors import InvalidInputError
from ep2fmu.resources import collect_model_resources


def test_collects_and_normalizes_window_data_file(tmp_path: Path) -> None:
    model = tmp_path / "building.idf"
    model.write_text("Version, 26.1;\n", encoding="utf-8")
    dataset_directory = tmp_path / "datasets"
    dataset_directory.mkdir()
    window_data = dataset_directory / "Window5DataFile.dat"
    window_data.write_bytes(b"window data\n")
    epjson = {
        "Construction:WindowDataFile": {
            "DoubleClear": {"file_name": r"datasets\Window5DataFile.dat"}
        }
    }

    resources = collect_model_resources(model, epjson)

    assert len(resources) == 1
    assert resources[0].source == window_data
    assert resources[0].name == "Window5DataFile.dat"
    assert epjson["Construction:WindowDataFile"]["DoubleClear"]["file_name"] == (
        "Window5DataFile.dat"
    )


def test_rejects_missing_model_resource(tmp_path: Path) -> None:
    model = tmp_path / "building.epJSON"
    model.write_text("{}\n", encoding="utf-8")
    epjson = {
        "Schedule:File": {
            "Occupancy": {"file_name": "missing.csv"},
        }
    }

    with pytest.raises(InvalidInputError, match="model resource does not exist"):
        collect_model_resources(model, epjson)

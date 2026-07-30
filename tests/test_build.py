from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from fmpy.validation import validate_fmu

from ep2fmu.api import build_fmu
from ep2fmu.constants import SUPPORTED_ENERGYPLUS_VERSION, Platform
from ep2fmu.energyplus import EnergyPlusInstallation
from ep2fmu.inspection import inspect_fmu
from ep2fmu.models import BuildOptions


def test_build_is_deterministic_and_inspectable(
        sample_model: Path,
        dummy_runtimes: Path,
        tmp_path: Path,
        monkeypatch,
) -> None:
    installation = EnergyPlusInstallation(
        home=tmp_path,
        executable=tmp_path / "energyplus",
        library=None,
        version="26.1.0",
    )
    monkeypatch.setattr("ep2fmu.api.resolve_energyplus", lambda _home: installation)

    def fake_convert(model: Path, _installation, workdir: Path) -> Path:
        destination = workdir / model.name
        shutil.copy2(model, destination)
        return destination

    monkeypatch.setattr("ep2fmu.api.convert_model", fake_convert)
    first = tmp_path / "first.fmu"
    second = tmp_path / "second.fmu"
    common = {
        "model_path": sample_model,
        "platforms": (Platform.LINUX64,),
    }
    result = build_fmu(BuildOptions(**common, output_path=first))
    build_fmu(BuildOptions(**common, output_path=second))

    assert first.read_bytes() == second.read_bytes()
    assert result.input_count == 1
    assert result.output_count == 1
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert "binaries/linux64/building.so" in archive.namelist()
        assert "resources/bin/linux64/ep2fmu-worker" in archive.namelist()
        config = archive.read("resources/ep2fmu-config.json")
        assert f'"energyplus_version":"{SUPPORTED_ENERGYPLUS_VERSION}"'.encode() in config
    details = inspect_fmu(first)
    assert details["model_identifier"] == "building"
    assert details["platforms"] == ["linux64"]
    assert validate_fmu(str(first)) == []

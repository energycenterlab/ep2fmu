#!/usr/bin/env python3
"""Normalize Cargo artifacts into the opaque runtime zip embedded in the wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

TIMESTAMP = (1980, 1, 1, 0, 0, 0)
SUFFIXES = {
    "linux64": ".so",
    "win64": ".dll",
    "darwin64": ".dylib",
    "darwinarm64": ".dylib",
}


def add(archive: zipfile.ZipFile, name: str, source: Path) -> str:
    info = zipfile.ZipInfo(name, TIMESTAMP)
    info.create_system = 3
    info.external_attr = 0o100755 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    data = source.read_bytes()
    archive.writestr(info, data)
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=sorted(SUFFIXES), required=True)
    parser.add_argument("--target-dir", type=Path, default=Path("target/release"))
    parser.add_argument("--output-dir", type=Path, default=Path("src/ep2fmu/runtime"))
    args = parser.parse_args()

    suffix = SUFFIXES[args.platform]
    if args.platform == "win64":
        library = args.target_dir / "ep2fmu_fmi2.dll"
        worker = args.target_dir / "ep2fmu-worker.exe"
    else:
        library = args.target_dir / f"libep2fmu_fmi2{suffix}"
        worker = args.target_dir / "ep2fmu-worker"
    missing = [path for path in (library, worker) if not path.is_file()]
    if missing:
        raise SystemExit(f"missing Cargo artifacts: {', '.join(map(str, missing))}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{args.platform}.zip"
    with zipfile.ZipFile(output, "w") as archive:
        checksums = {
            f"ep2fmu_fmi2{suffix}": add(archive, f"ep2fmu_fmi2{suffix}", library),
            worker.name: add(archive, worker.name, worker),
        }
        info = zipfile.ZipInfo("checksums.json", TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(
            info,
            json.dumps(checksums, indent=2, sort_keys=True).encode() + b"\n",
        )
    print(output)


if __name__ == "__main__":
    main()

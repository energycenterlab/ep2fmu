#!/usr/bin/env python3
"""Merge x86_64 and arm64 runtime bundles into deterministic universal2 zips."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import zipfile
from pathlib import Path

from package_runtime import TIMESTAMP, add


def extract_member(bundle: Path, member: str, destination: Path) -> Path:
    output = destination / f"{bundle.stem}-{member}"
    with zipfile.ZipFile(bundle) as archive:
        output.write_bytes(archive.read(member))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--x64", type=Path, required=True)
    parser.add_argument("--arm64", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("src/ep2fmu/runtime"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ep2fmu-universal-") as temporary:
        root = Path(temporary)
        merged: dict[str, Path] = {}
        for member in ("ep2fmu_fmi2.dylib", "ep2fmu-worker"):
            x64 = extract_member(args.x64, member, root)
            arm64 = extract_member(args.arm64, member, root)
            output = root / member
            subprocess.run(
                ["lipo", "-create", str(x64), str(arm64), "-output", str(output)],
                check=True,
            )
            merged[member] = output

        # Both public selectors resolve to the same FMI 2 darwin64 universal bundle.
        for platform in ("darwin64", "darwinarm64"):
            output = args.output_dir / f"{platform}.zip"
            with zipfile.ZipFile(output, "w") as archive:
                for member, source in sorted(merged.items()):
                    add(archive, member, source)
                info = zipfile.ZipInfo("universal2", TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, b"x86_64+arm64\n")
            print(output)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compare ep2fmu export time with lbl-srg/EnergyPlusToFMU.

The benchmark deliberately uses one EnergyPlus 26.1 IDF, one weather file and
two legacy output mappings for both exporters. On Apple Silicon the legacy tool
must run under Rosetta because its vendored libxml2 is x86_64-only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

LEGACY_MAPPINGS = """

ExternalInterface,
  FunctionalMockupUnitExport;

ExternalInterface:FunctionalMockupUnitExport:From:Variable,
  ZONE ONE,
  Zone Mean Air Temperature,
  indoorTemperature;

ExternalInterface:FunctionalMockupUnitExport:From:Variable,
  ZONE ONE,
  Zone Air Relative Humidity,
  indoorRelativeHumidity;
"""


@dataclass(frozen=True)
class Observation:
    exporter: str
    iteration: int
    elapsed_seconds: float
    success: bool
    fmu_bytes: int | None
    error: str | None = None


def run_timed(command: list[str], cwd: Path, output: Path) -> Observation:
    started = time.perf_counter()
    process = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    elapsed = time.perf_counter() - started
    error = None
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip().splitlines()
        error = detail[-1] if detail else f"exit code {process.returncode}"
    return Observation(
        exporter="",
        iteration=0,
        elapsed_seconds=elapsed,
        success=process.returncode == 0 and output.is_file(),
        fmu_bytes=output.stat().st_size if output.is_file() else None,
        error=error,
    )


def benchmark(
        *,
        project_root: Path,
        legacy_root: Path,
        energyplus_home: Path,
        repetitions: int,
) -> tuple[list[Observation], dict[str, object]]:
    reference = energyplus_home / "ExampleFiles" / "1ZoneUncontrolled.idf"
    weather = energyplus_home / "WeatherData" / "USA_CO_Golden-NREL.724666_TMY3.epw"
    idd = energyplus_home / "Energy+.idd"
    for required in (reference, weather, idd):
        if not required.is_file():
            raise FileNotFoundError(required)

    observations: list[Observation] = []
    with tempfile.TemporaryDirectory(prefix="ep2fmu-export-benchmark-") as temporary:
        root = Path(temporary)
        model = root / "BenchmarkOneZone.idf"
        model.write_text(
            reference.read_text(encoding="utf-8") + LEGACY_MAPPINGS,
            encoding="utf-8",
        )
        modern_work = root / "modern"
        legacy_work = root / "legacy"
        modern_work.mkdir()
        legacy_work.mkdir()

        modern_output = modern_work / "BenchmarkOneZone.fmu"
        modern_command = [
            str(project_root / ".venv" / "bin" / "ep2fmu"),
            "build",
            str(model),
            "--weather",
            str(weather),
            "--energyplus-home",
            str(energyplus_home),
            "--platform",
            current_ep2fmu_platform(),
            "--output",
            str(modern_output),
        ]

        legacy_output = legacy_work / "BenchmarkOneZone.fmu"
        legacy_python = [sys.executable]
        legacy_architecture = platform.machine()
        if sys.platform == "darwin" and platform.machine() == "arm64":
            legacy_python = ["arch", "-x86_64", "/usr/bin/python3"]
            legacy_architecture = "x86_64 (Rosetta)"
        legacy_command = [
            *legacy_python,
            str(legacy_root / "Scripts" / "EnergyPlusToFMU.py"),
            "-i",
            str(idd),
            "-w",
            str(weather),
            "-a",
            "2",
            str(model),
        ]

        for iteration in range(1, repetitions + 1):
            modern = run_timed(modern_command, modern_work, modern_output)
            observations.append(
                Observation(
                    **{
                        **asdict(modern),
                        "exporter": "ep2fmu",
                        "iteration": iteration,
                    }
                )
            )
            legacy = run_timed(legacy_command, legacy_work, legacy_output)
            observations.append(
                Observation(
                    **{
                        **asdict(legacy),
                        "exporter": "EnergyPlusToFMU",
                        "iteration": iteration,
                    }
                )
            )

        metadata = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "repetitions": repetitions,
            "model": str(reference),
            "weather": str(weather),
            "energyplus_version": subprocess.run(
                [str(energyplus_home / "energyplus"), "--version"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip(),
            "host": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            "legacy_revision": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=legacy_root,
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip(),
            "legacy_architecture": legacy_architecture,
            "modern_platform": current_ep2fmu_platform(),
        }
    return observations, metadata


def current_ep2fmu_platform() -> str:
    if sys.platform == "darwin":
        return "darwinarm64" if platform.machine() == "arm64" else "darwin64"
    if sys.platform.startswith("win"):
        return "win64"
    return "linux64"


def summarize(observations: list[Observation]) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for exporter in ("ep2fmu", "EnergyPlusToFMU"):
        selected = [
            observation
            for observation in observations
            if observation.exporter == exporter and observation.success
        ]
        times = [observation.elapsed_seconds for observation in selected]
        if not times:
            continue
        sizes = [
            observation.fmu_bytes for observation in selected if observation.fmu_bytes is not None
        ]
        summary[exporter] = {
            "successful_runs": len(selected),
            "median_seconds": statistics.median(times),
            "mean_seconds": statistics.mean(times),
            "min_seconds": min(times),
            "max_seconds": max(times),
            "stdev_seconds": statistics.stdev(times) if len(times) > 1 else 0.0,
            "median_fmu_bytes": int(statistics.median(sizes)),
        }
    if set(summary) == {"ep2fmu", "EnergyPlusToFMU"}:
        summary["comparison"] = {
            "median_speedup": (
                    float(summary["EnergyPlusToFMU"]["median_seconds"])
                    / float(summary["ep2fmu"]["median_seconds"])
            ),
            "median_time_reduction_percent": (
                                                     1.0
                                                     - float(summary["ep2fmu"]["median_seconds"])
                                                     / float(summary["EnergyPlusToFMU"]["median_seconds"])
                                             )
                                             * 100.0,
        }
    return summary


def write_csv(path: Path, observations: list[Observation]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=Observation.__dataclass_fields__)
        writer.writeheader()
        for observation in observations:
            writer.writerow(asdict(observation))


def write_svg(
        path: Path,
        observations: list[Observation],
        summary: dict[str, dict[str, float | int]],
) -> None:
    width, height = 980, 520
    left, right, top, bottom = 90, 40, 55, 80
    plot_width = width - left - right
    plot_height = height - top - bottom
    successful = [item for item in observations if item.success]
    maximum = max(item.elapsed_seconds for item in successful)
    axis_max = math.ceil(maximum / 5.0) * 5.0
    colors = {"ep2fmu": "#2563eb", "EnergyPlusToFMU": "#f97316"}
    groups = ("ep2fmu", "EnergyPlusToFMU")
    group_centers = (left + plot_width * 0.28, left + plot_width * 0.72)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        '<title id="title">Tempo di export FMU</title>',
        '<desc id="description">Confronto tra ep2fmu e EnergyPlusToFMU legacy.</desc>',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="490" y="28" text-anchor="middle" font-family="sans-serif" '
        'font-size="20" font-weight="600">Tempo di export FMU — stesso modello e meteo</text>',
    ]
    for tick in range(0, int(axis_max) + 1, 5):
        y = top + plot_height - (tick / axis_max) * plot_height
        lines.extend(
            [
                f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
                'stroke="#d1d5db" stroke-width="1"/>',
                f'<text x="{left - 12}" y="{y + 5:.1f}" text-anchor="end" '
                f'font-family="sans-serif" font-size="13" fill="#374151">{tick}</text>',
            ]
        )
    lines.append(
        f'<text x="24" y="{top + plot_height / 2:.1f}" text-anchor="middle" '
        'font-family="sans-serif" font-size="14" fill="#111827" '
        'transform="rotate(-90 24 '
        f'{top + plot_height / 2:.1f})">secondi (meno è meglio)</text>'
    )

    for exporter, center in zip(groups, group_centers, strict=True):
        selected = [item for item in successful if item.exporter == exporter]
        median = float(summary[exporter]["median_seconds"])
        bar_width = 170
        bar_height = (median / axis_max) * plot_height
        bar_y = top + plot_height - bar_height
        lines.extend(
            [
                f'<rect x="{center - bar_width / 2:.1f}" y="{bar_y:.1f}" '
                f'width="{bar_width}" height="{bar_height:.1f}" '
                f'fill="{colors[exporter]}" opacity="0.82"/>',
                f'<text x="{center:.1f}" y="{bar_y - 12:.1f}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="18" font-weight="600" '
                f'fill="#111827">{median:.3f} s</text>',
                f'<text x="{center:.1f}" y="{height - bottom + 30}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="15" fill="#111827">'
                f"{escape(exporter)}</text>",
            ]
        )
        count = len(selected)
        for index, observation in enumerate(selected):
            offset = (index - (count - 1) / 2) * 18
            y = top + plot_height - (observation.elapsed_seconds / axis_max) * plot_height
            lines.append(
                f'<circle cx="{center + offset:.1f}" cy="{y:.1f}" r="5" '
                f'fill="{colors[exporter]}" stroke="white" stroke-width="1.5"/>'
            )

    speedup = float(summary["comparison"]["median_speedup"])
    reduction = float(summary["comparison"]["median_time_reduction_percent"])
    lines.extend(
        [
            f'<text x="{width / 2:.1f}" y="{height - 18}" text-anchor="middle" '
            'font-family="sans-serif" font-size="14" fill="#374151">'
            f"ep2fmu: {speedup:.1f}x più veloce; tempo ridotto del {reduction:.1f}%"
            "</text>",
            "</svg>",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--legacy-repo",
        type=Path,
        default=Path("/private/tmp/EnergyPlusToFMU-legacy"),
    )
    parser.add_argument(
        "--energyplus-home",
        type=Path,
        default=Path("/Applications/EnergyPlus-26-1-0"),
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "results",
    )
    args = parser.parse_args()
    if args.repetitions < 3:
        raise SystemExit("at least three repetitions are required")

    project_root = Path(__file__).resolve().parents[1]
    observations, metadata = benchmark(
        project_root=project_root,
        legacy_root=args.legacy_repo.resolve(),
        energyplus_home=args.energyplus_home.resolve(),
        repetitions=args.repetitions,
    )
    summary = summarize(observations)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "export-times.csv", observations)
    (args.output_dir / "export-benchmark.json").write_text(
        json.dumps(
            {
                "metadata": metadata,
                "summary": summary,
                "observations": [asdict(x) for x in observations],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if "comparison" in summary:
        write_svg(args.output_dir / "export-time-comparison.svg", observations, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if any(not observation.success for observation in observations):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

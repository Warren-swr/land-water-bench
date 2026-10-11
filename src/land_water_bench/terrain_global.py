"""Global terrain task: every original grid point, with explicit height-reference coverage."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from importlib.resources import files
from pathlib import Path

from .core import digest, make_grid
from .runner import Runner, SOURCE_FILES
from .terrain import PARSER_VERSION, TOLERANCES, project_answer

PROTOCOL = "terrain-elevation/1.0-global-center-2deg"
SCORE_VERSION = "terrain-score/2.0-area-weighted-reference-mask"
PROMPT = (
    "Latitude: {lat:.1f}, Longitude: {lon:.1f}. "
    "Predict the surface type (Solid or Water) and terrain elevation in meters relative to mean sea level "
    "(EGM2008). Solid includes land and grounded ice: report bare-ground elevation, or the ice-surface "
    "elevation on grounded ice. Water includes oceans, lakes, and floating ice shelves: report the seabed "
    "or lakebed elevation, NOT the water or floating-ice surface elevation. "
    "Underwater terrain below sea level has a negative elevation. Ignore buildings, vegetation, and temporary sea ice. "
    'Reply with exactly one JSON object with two fields: "type" ("Solid" or "Water") '
    'and "elevation_m" (a single number in meters, not a range).'
)


def score(points, records):
    total_weight = math.fsum(p["weight"] for p in points)
    reference = [p for p in points if p["height_reference_status"] == "ok"]
    reference_weight = math.fsum(p["weight"] for p in reference)
    pending = sum(p["id"] not in records for p in points)
    valid = invalid = infra = classification_correct = 0
    included_weight = correct_weight = joint_weight = numeric_weight = 0.0
    eligible_included = 0
    errors, weighted_errors = [], []
    joint = {str(t): 0 for t in TOLERANCES}
    weighted_joint = {str(t): 0.0 for t in TOLERANCES}
    for point in points:
        row = records.get(point["id"])
        if row is None:
            continue
        if row["status"] == "infra_error":
            infra += 1
            continue
        included_weight += point["weight"]
        height_ok = point["height_reference_status"] == "ok"
        if height_ok:
            eligible_included += 1
            joint_weight += point["weight"]
        if row["status"] != "ok":
            invalid += 1
            continue
        valid += 1
        correct = row["label"] == point["truth"]
        classification_correct += correct
        correct_weight += point["weight"] * correct
        if not height_ok:
            continue
        error = abs(row["prediction"]["elevation_m"] - point["elevation_m"])
        errors.append(error)
        weighted_errors.append(error * point["weight"])
        numeric_weight += point["weight"]
        for tolerance in TOLERANCES:
            success = bool(correct and error <= tolerance)
            joint[str(tolerance)] += success
            weighted_joint[str(tolerance)] += point["weight"] * success
    scored = valid + invalid
    final = pending == 0
    ratio = lambda a, b: a / b if b else None
    return {"expected": len(points), "valid": valid, "invalid": invalid, "infra_errors": infra,
            "pending": pending, "scored": scored, "complete": final,
            "coverage": ratio(scored, len(points)), "area_coverage": ratio(included_weight, total_weight),
            "height_reference_eligible": len(reference), "height_reference_excluded": len(points)-len(reference),
            "height_reference_area_coverage": ratio(reference_weight, total_weight),
            "joint_scored": eligible_included, "joint_area_coverage": ratio(joint_weight, reference_weight),
            "numeric_coverage": ratio(len(errors), eligible_included),
            "classification_correct": classification_correct,
            "classification_accuracy": ratio(correct_weight, included_weight) if final else None,
            "point_classification_accuracy": ratio(classification_correct, scored) if final else None,
            "observed_classification_accuracy": ratio(correct_weight, included_weight),
            "joint_correct": joint,
            "joint_accuracy": {str(t): ratio(weighted_joint[str(t)], joint_weight) if final else None for t in TOLERANCES},
            "observed_joint_accuracy": {str(t): ratio(weighted_joint[str(t)], joint_weight) for t in TOLERANCES},
            "point_joint_accuracy": {str(t): ratio(joint[str(t)], eligible_included) if final else None for t in TOLERANCES},
            "elevation_mae_m": ratio(math.fsum(weighted_errors), numeric_weight),
            "point_elevation_mae_m": statistics.fmean(errors) if errors else None,
            "elevation_median_ae_m": statistics.median(errors) if errors else None,
            "elevation_p90_ae_m": sorted(errors)[max(0, math.ceil(len(errors)*.9)-1)] if errors else None}


def metrics(points, records):
    result = score(points, records)
    result["by_type"] = {kind: score([p for p in points if p["truth"] == label], records)
                         for kind, label in [("Solid", 1), ("Water", 0)]}
    result["by_stratum"] = {name: score([p for p in points if p["stratum"] == name], records)
                            for name in sorted({p["stratum"] for p in points})}
    return result


def validate_dataset(dataset):
    if dataset.get("protocol") != PROTOCOL:
        raise ValueError("Expected the global terrain protocol")
    grid = make_grid()
    points = dataset["points"]
    if len(points) != len(grid):
        raise ValueError("Global terrain requires all 16,200 points")
    for point, expected in zip(points, grid):
        if any(point[k] != expected[k] for k in ("id", "lat", "lon", "weight")):
            raise ValueError("Global grid coordinates or area weights differ")
        if point["truth"] not in (0, 1) or point["prompt"] != PROMPT.format(**point):
            raise ValueError("Invalid classification reference or prompt")
        if point["height_reference_status"] == "ok":
            if not isinstance(point["elevation_m"], (int, float)) or not math.isfinite(point["elevation_m"]):
                raise ValueError("Missing reference height")
        elif point["elevation_m"] is not None or not point.get("height_reference_reason"):
            raise ValueError("Unscorable reference must be null and have an explicit reason")
    return dataset


class GlobalRunner(Runner):
    protocol = PROTOCOL
    answer_version = PARSER_VERSION
    score_version = SCORE_VERSION
    source_url = "https://www.ncei.noaa.gov/products/etopo-global-relief-model"
    source_files = SOURCE_FILES + ("terrain.py", "terrain_global.py")

    def make_dataset(self):
        return validate_dataset(json.loads(self.args.dataset.read_text()))

    def config_updates(self):
        return {"input_dataset_sha256": digest(self.args.dataset.read_bytes()),
                "primary_metric": "area_weighted_joint_accuracy.500", "tolerances_m": list(TOLERANCES),
                "height_reference_mask": "frozen before model calls", "all_grid_points_requested": not self.args.limit}

    def project_answer(self, row, wire):
        return project_answer(row, wire)

    def score_records(self, points, records):
        return metrics(points, records)



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--dataset", type=Path,
                        default=Path(str(files("land_water_bench").joinpath("data/terrain-global-dataset.json"))))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--concurrency", type=int, default=512)
    parser.add_argument("--max-retries", type=int, default=1000)
    parser.add_argument("--max-timeout-retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=65536)
    parser.add_argument("--resume-revision")
    args = parser.parse_args(argv)
    if not 1 <= args.concurrency <= 512 or not 0 <= args.max_retries <= 1000 or args.max_timeout_retries < 0:
        parser.error("Invalid concurrency or retry limit")
    if not 0 <= args.limit <= 16200 or args.timeout <= 0 or args.max_output_tokens < 1:
        parser.error("Invalid sample limit, timeout or output cap")
    args.export_dir = args.export_dir or args.run_dir / "snapshot"
    asyncio.run(GlobalRunner(args).execute())


if __name__ == "__main__":
    main()

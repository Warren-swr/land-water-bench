"""Display-only summaries for the unified page, derived from frozen terrain responses."""
from __future__ import annotations

import math

from .results import require
from .terrain import TOLERANCES

TOLERANCE_STEPS = tuple(range(100, 2001, 100))
HISTOGRAM = {"start": -11000, "stop": 9000, "step": 100}
COLUMNS = 180


def tolerance_curve(points, records, tolerances=TOLERANCE_STEPS):
    """Area-weighted joint accuracy using the same inclusion rules as the official score."""
    joint_weight = 0.0
    weighted = {t: 0.0 for t in tolerances}
    for point in points:
        row = records.get(point["id"])
        if row is None or row["status"] == "infra_error" or point["height_reference_status"] != "ok":
            continue
        joint_weight += point["weight"]
        if row["status"] != "ok" or row["label"] != point["truth"]:
            continue
        error = abs(row["prediction"]["elevation_m"] - point["elevation_m"])
        for tolerance in tolerances:
            weighted[tolerance] += point["weight"] * (error <= tolerance)
    return [weighted[t] / joint_weight if joint_weight else None for t in tolerances]


def grid(points, records):
    """Compact per-point predictions: exact height (integers unboxed) and a one-letter state per cell."""
    heights, states = [], []
    for point in points:
        row = records.get(point["id"])
        status = row["status"] if row else None
        if status == "ok":
            states.append("S" if row["label"] else "W")
            value = row["prediction"]["elevation_m"]
            finite = value is not None and math.isfinite(value)
            heights.append(int(value) if finite and float(value).is_integer() else value if finite else None)
        else:
            states.append({"invalid": "I", "infra_error": "U"}.get(status, "-"))
            heights.append(None)
    return {"elevation_m": heights, "state": "".join(states)}


def histogram(values):
    start, stop, step = HISTOGRAM["start"], HISTOGRAM["stop"], HISTOGRAM["step"]
    counts = [0] * ((stop - start) // step)
    for value in values:
        counts[min(len(counts) - 1, max(0, int((value - start) // step)))] += 1
    return counts


def roughness(points, heights):
    """Mean absolute east-west neighbour difference over reference water cells."""
    if len(points) % COLUMNS:
        return None
    total = count = 0
    for point in points:
        neighbour = points[point["id"] - point["id"] % COLUMNS + (point["id"] + 1) % COLUMNS]
        a, b = heights[point["id"]], heights[neighbour["id"]]
        if point["truth"] == 0 and neighbour["truth"] == 0 and a is not None and b is not None:
            total += abs(a - b)
            count += 1
    return total / count if count else None


def profile(points, records):
    numeric = {p["id"]: row["prediction"]["elevation_m"] for p in points
               if (row := records.get(p["id"])) and row["status"] == "ok"
               and row["prediction"].get("elevation_m") is not None}
    values = list(numeric.values())
    share = lambda multiple: sum(v % multiple == 0 for v in values) / len(values) if values else None
    heights = [numeric.get(p["id"]) for p in points]
    large = sum(p["height_reference_status"] == "ok" and p["id"] in numeric
                and abs(numeric[p["id"]] - p["elevation_m"]) > 2000 for p in points)
    return {"multiple_100": share(100), "multiple_500": share(500), "multiple_1000": share(1000),
            "distinct_values": len(set(values)), "seabed_roughness_m": roughness(points, heights),
            "errors_over_2000_m": large, "histogram": histogram(values)}


def reference_profile(points):
    heights = [p["elevation_m"] if p["height_reference_status"] == "ok" else None for p in points]
    return {"seabed_roughness_m": roughness(points, heights),
            "histogram": histogram([h for h in heights if h is not None])}


def build_atlas(points, models):
    """`models` is a list of (name, official_metrics, records) in leaderboard order."""
    result = {"schema": "land-water-atlas/1.0", "tolerances_m": list(TOLERANCE_STEPS), "official_tolerance_m": 500,
              "histogram": HISTOGRAM, "reference": reference_profile(points), "models": {}}
    for name, metrics, records in models:
        curve = tolerance_curve(points, records)
        for tolerance in TOLERANCES:
            official = metrics["joint_accuracy"][str(tolerance)]
            require(math.isclose(curve[TOLERANCE_STEPS.index(tolerance)], official, rel_tol=0, abs_tol=1e-12),
                    "Tolerance curve disagrees with the official score")
        result["models"][name] = {"tolerance_curve": curve, "profile": profile(points, records),
                                  "grid": grid(points, records)}
    return result

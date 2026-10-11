"""Freeze a balanced pilot using GSHHG labels and ETOPO 2022 terrain heights."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import math
import random
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .core import atomic_json, digest, make_dataset
from .terrain import PROMPT, PROTOCOL, validate_dataset

BASE = "https://oceanwatch.pifsc.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s"
STRATA = ("solid_low", "solid_mid", "solid_high", "solid_alpine",
          "water_shelf", "water_slope", "water_deep", "water_abyssal")


def prepare_sources(sources):
    """Download the small candidate grid and metadata, retaining exact source bytes."""
    sources.mkdir(parents=True, exist_ok=True)
    if (sources / "candidates-download.json").exists():
        return
    url = BASE.replace("15s", "60s") + ".csv?z[(-59):120:(59)][(1):120:(359)]"
    with httpx.Client(timeout=180, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        parse_csv(response.content)
        (sources / "candidates-60s.csv").write_bytes(response.content)
        for extension in ("dds", "das"):
            metadata = client.get(BASE + "." + extension)
            metadata.raise_for_status()
            (sources / ("metadata." + extension)).write_bytes(metadata.content)
    atomic_json(sources / "candidates-download.json", {
        "file": "candidates-60s.csv", "url": url, "sha256": digest(response.content),
        "bytes": len(response.content), "retrieved_at": datetime.now(timezone.utc).isoformat()})


def stratum(label, elevation):
    if label:
        return STRATA[0 if elevation < 200 else 1 if elevation < 1000 else 2 if elevation < 2500 else 3]
    depth = -elevation
    return STRATA[4 if depth < 500 else 5 if depth < 2500 else 6 if depth < 4500 else 7]


def parse_csv(raw):
    reader = csv.reader(io.StringIO(raw.decode()))
    if next(reader) != ["latitude", "longitude", "z"]:
        raise ValueError("Unexpected ETOPO CSV schema")
    next(reader)
    values = [tuple(map(float, row)) for row in reader]
    if not values or not all(math.isfinite(n) for row in values for n in row):
        raise ValueError("Missing or nonfinite ETOPO values")
    return values


def point_height(raw, latitude, longitude):
    rows = parse_csv(raw)
    longitude %= 360
    if len(rows) != 4:
        raise ValueError("Expected four native 15 arc-second cells")
    expected = {(round(latitude + a / 480, 7), round(longitude + b / 480, 7))
                for a in (-1, 1) for b in (-1, 1)}
    actual = {(round(lat, 7), round(lon, 7)) for lat, lon, _ in rows}
    if actual != expected:
        raise ValueError("ETOPO registration mismatch")
    heights = [row[2] for row in rows]
    return sum(heights) / 4, max(heights) - min(heights), rows


async def download_points(points, output):
    output.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        async def fetch(point):
            target = output / f"{point['id']}.csv"
            lat = int((point["lat"] + 90) * 240) - 1
            lon = int((point["lon"] % 360) * 240) - 1
            url = f"{BASE}.csv?z[{lat}:1:{lat+1}][{lon}:1:{lon+1}]"
            if not target.exists():
                async with semaphore:
                    for attempt in range(4):
                        try:
                            response = await client.get(url)
                            response.raise_for_status()
                            point_height(response.content, point["lat"], point["lon"])
                            target.write_bytes(response.content)
                            break
                        except (httpx.HTTPError, ValueError):
                            if attempt == 3:
                                raise
                            await asyncio.sleep(2 ** attempt)
            raw = target.read_bytes()
            elevation, spread, rows = point_height(raw, point["lat"], point["lon"])
            return {**point, "elevation_m": elevation, "native_cell_spread_m": spread,
                    "native_cells": rows, "source_url": url, "source_sha256": digest(raw)}
        return await asyncio.gather(*(fetch(point) for point in points))


def build(sources, details_path, output, per_stratum=64, seed=20261010):
    if output.exists():
        raise ValueError("Use a new dataset path; references are immutable")
    if (sources / "FILES.sha256").exists():
        raise ValueError("Reference sources are sealed; use a new directory")
    prepare_sources(sources)
    base = make_dataset()
    details = json.loads(details_path.read_text())
    download = json.loads((sources / "candidates-download.json").read_text())
    raw = (sources / download["file"]).read_bytes()
    if digest(raw) != download["sha256"]:
        raise ValueError("Candidate source hash mismatch")
    candidates = {(int(round(lat)), (int(round(lon)) + 180) % 360 - 180): z
                  for lat, lon, z in parse_csv(raw)}
    groups = defaultdict(list)
    excluded = Counter()
    for point in base["points"]:
        detail = details[str(point["id"])]
        key = (point["lat"], point["lon"])
        if abs(point["lat"]) > 59 or key not in candidates:
            excluded["outside_59_degree_latitude_scope"] += 1
            continue
        if detail["category"] not in {"land", "ocean"}:
            excluded["lake_ice_or_nested_island"] += 1
            continue
        if detail["comparison_disagreement"] or detail["exact_boundary_tie"]:
            excluded["classification_reference_disagreement_or_boundary"] += 1
            continue
        z = candidates[key]
        if abs(z) < 25 or (not point["truth"] and z >= 0):
            excluded["near_zero_or_ocean_height_conflict"] += 1
            continue
        groups[stratum(point["truth"], z)].append({**point, "selection_elevation_60s_m": z})
    rng = random.Random(seed)
    selected = []
    for name in STRATA:
        rng.shuffle(groups[name])
        if len(groups[name]) < per_stratum:
            raise ValueError(f"Not enough candidates in {name}: {len(groups[name])}")
        selected.extend(groups[name][:per_stratum + 16])
    atomic_json(sources / "selection-plan.json", {"seed": seed, "per_stratum": per_stratum,
                "candidate_counts": {s: len(groups[s]) for s in STRATA}, "excluded": dict(excluded),
                "download_ids": [p["id"] for p in selected]})
    print(json.dumps({"stage": "download_native_cells", "points": len(selected),
                      "candidate_counts": {s: len(groups[s]) for s in STRATA}}), flush=True)
    sampled = asyncio.run(download_points(selected, sources / "points"))
    native_groups = defaultdict(list)
    for point in sampled:
        if abs(point["elevation_m"]) < 25 or (not point["truth"] and point["elevation_m"] >= 0):
            continue
        native_groups[stratum(point["truth"], point["elevation_m"])].append(point)
    points = []
    for name in STRATA:
        group = native_groups[name]
        if len(group) < per_stratum:
            raise ValueError(f"Native cells left fewer than {per_stratum} in {name}: {len(group)}")
        rng.shuffle(group)
        for point in group[:per_stratum]:
            points.append({"id": len(points), "source_grid_id": point["id"],
                           "lat": point["lat"], "lon": point["lon"], "truth": point["truth"],
                           "weight": 1, "stratum": name, "elevation_m": point["elevation_m"],
                           "native_cell_spread_m": point["native_cell_spread_m"],
                           "native_cells": point["native_cells"],
                           "source_url": point["source_url"], "source_sha256": point["source_sha256"],
                           "prompt": PROMPT.format(**point)})
    dataset = {"protocol": PROTOCOL, "created_at": datetime.now(timezone.utc).isoformat(),
               "prompt_template": PROMPT, "sampling_seed": seed, "per_stratum": per_stratum,
               "scope": "Latitude -59..59; GSHHG ordinary land and ocean; no known reference disagreements; abs(elevation)>=25m",
               "sampling": "60 arc-second candidate strata; final re-stratification with bilinear 15 arc-second heights; equal counts per stratum; not an area-representative world sample",
               "height_reference": {"name": "NOAA ETOPO 2022", "version": "ice surface, 15 arc-second",
                   "datum": "EGM2008", "units": "meters", "interpolation": "bilinear mean of four cells surrounding each integer coordinate",
                   "source": BASE + ".html", "candidate_source": download,
                   "uncertainty": "Grid reference, not exact physical ground truth. Native cell spread measures local variation, not measurement uncertainty."},
               "classification_reference": base["reference"],
               "classification_details_sha256": digest(details_path.read_bytes()),
               "points": points}
    validate_dataset(dataset)
    atomic_json(output, dataset)
    shutil.copy2(Path(__file__), sources / "terrain_reference.py")
    atomic_json(sources / "build-manifest.json", {"dataset_sha256": digest(output.read_bytes()),
                "builder_sha256": digest(Path(__file__).read_bytes()), "excluded": dict(excluded),
                "source_metadata_sha256": {p.name: digest(p.read_bytes()) for p in sources.glob("metadata.*")},
                "strata": dict(Counter(p["stratum"] for p in points)), "new_model_requests": 0})
    print(json.dumps({"dataset": str(output), "sha256": digest(output.read_bytes()), "points": len(points)}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sources", type=Path, required=True)
    p.add_argument("--classification-details", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--per-stratum", type=int, default=64)
    args = p.parse_args()
    build(args.sources, args.classification_details, args.output, args.per_stratum)


if __name__ == "__main__":
    main()

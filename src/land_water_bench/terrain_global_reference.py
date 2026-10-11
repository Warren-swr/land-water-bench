"""Download two native rows around each latitude, preserving all 16,200 coordinates."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil

import httpx

from .core import atomic_json, digest, make_dataset
from .terrain_global import PROMPT, PROTOCOL, validate_dataset
from .terrain_reference import BASE, parse_csv, stratum


def row_values(raw, latitude, side):
    values = parse_csv(raw)
    if len(values) != 360:
        raise ValueError("Expected two rows by 180 longitudes")
    expected_latitudes = {round(latitude + d / 480, 7) for d in (-1, 1)}
    expected_longitudes = {round(lon + (-1 if side == 0 else 1) / 480, 7) for lon in range(1, 360, 2)}
    if {round(p[0], 7) for p in values} != expected_latitudes or {round(p[1], 7) for p in values} != expected_longitudes:
        raise ValueError("Native row registration mismatch")
    return {(round(lat, 7), round(lon, 7)): z for lat, lon, z in values}


async def download_rows(directory):
    rows = directory / "rows"
    rows.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(6)
    completed = 0
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        async def fetch(latitude, side):
            nonlocal completed
            target = rows / f"lat{latitude:+03d}-side{side}.csv"
            start = int((latitude + 90) * 240) - 1
            url = f"{BASE}.csv?z[{start}:1:{start+1}][{239+side}:480:{86159+side}]"
            if not target.exists():
                async with semaphore:
                    for attempt in range(5):
                        try:
                            response = await client.get(url)
                            response.raise_for_status()
                            row_values(response.content, latitude, side)
                            target.write_bytes(response.content)
                            break
                        except (httpx.HTTPError, ValueError):
                            if attempt == 4:
                                raise
                            await asyncio.sleep(2 ** attempt)
            raw = target.read_bytes()
            row_values(raw, latitude, side)
            completed += 1
            if completed % 10 == 0:
                print(json.dumps({"stage":"reference_rows","completed":completed,"total":180}), flush=True)
            return {"file":str(target.relative_to(directory)),"latitude":latitude,"side":side,"url":url,"sha256":digest(raw)}
        return await asyncio.gather(*(fetch(latitude, side) for latitude in range(-89, 90, 2) for side in (0, 1)))


def build(directory, details_path):
    if (directory / "dataset.json").exists() or (directory / "FILES.sha256").exists():
        raise ValueError("Use a fresh, unsealed reference directory")
    directory.mkdir(parents=True, exist_ok=True)
    sources = asyncio.run(download_rows(directory))
    atomic_json(directory / "downloads.json", {"retrieved_at":datetime.now(timezone.utc).isoformat(),"files":sources})
    values, files = {}, {}
    for source in sources:
        raw = (directory / source["file"]).read_bytes()
        values.update(row_values(raw, source["latitude"], source["side"]))
        files[source["latitude"], source["side"]] = source
    base = make_dataset()
    details = json.loads(details_path.read_text())
    points = []
    exclusions = Counter()
    for point in base["points"]:
        latitude, longitude = point["lat"], point["lon"] % 360
        cells = [[latitude+a/480, longitude+b/480, values[round(latitude+a/480,7),round(longitude+b/480,7)]] for a in (-1,1) for b in (-1,1)]
        height = math.fsum(c[2] for c in cells) / 4
        detail = details[str(point["id"])]
        reasons = []
        if detail["category"] not in {"land", "ocean", "grounded_ice"}:
            reasons.append("unsupported_" + detail["category"])
        if detail["comparison_disagreement"] or detail.get("exact_boundary_tie", False):
            reasons.append("classification_reference_disagreement_or_boundary")
        if detail["category"] == "ocean" and height >= 0:
            reasons.append("ocean_bed_vs_surface_height_conflict")
        reason = ";".join(reasons)
        if reason:
            exclusions[reason] += 1
        points.append({**point, "source_grid_id":point["id"], "category":detail["category"],
                       "elevation_m":None if reason else height, "raw_etopo_elevation_m":height,
                       "height_reference_status":"unscorable" if reason else "ok",
                       "height_reference_reason":reason or None,
                       "stratum":"reference_unscorable" if reason else stratum(point["truth"],height),
                       "native_cell_spread_m":max(c[2] for c in cells)-min(c[2] for c in cells),
                       "native_cells":cells, "reference_files":[files[latitude,s]["file"] for s in (0,1)],
                       "prompt":PROMPT.format(**point)})
    dataset = {"protocol":PROTOCOL,"created_at":datetime.now(timezone.utc).isoformat(),
               "prompt_template":PROMPT,"sampling":"all original 16,200 grid centers; no coordinate exclusions",
               "width":180,"height":90,"step_degrees":2,"weighting":"cos(latitude)",
               "height_reference":{"name":"NOAA ETOPO 2022","version":"15 arc-second ice surface","datum":"EGM2008",
                   "units":"meters","interpolation":"bilinear mean of four surrounding native cells",
                   "source":BASE+".html","grounded_ice":"ice surface elevation",
                   "unscorable_policy":"lakes, floating ice shelves, nested islands, known classification disagreements and water/nonnegative-height conflicts; requests and classification remain included"},
               "classification_reference":base["reference"],"classification_details_sha256":digest(details_path.read_bytes()),
               "height_reference_exclusions":dict(exclusions),"points":points}
    validate_dataset(dataset)
    atomic_json(directory / "dataset.json", dataset)
    for name in ("terrain_global_reference.py","terrain_global.py","terrain_reference.py"):
        shutil.copy2(Path(__file__).parent/name,directory/name)
    atomic_json(directory/"build-manifest.json",{"dataset_sha256":digest((directory/"dataset.json").read_bytes()),
                "requested_points":len(points),"height_scorable":sum(p["height_reference_status"]=="ok" for p in points),
                "height_exclusions":dict(exclusions),"source_count":len(sources),"new_model_requests":0})
    print(json.dumps(json.loads((directory/"build-manifest.json").read_text())),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--classification-details',type=Path,required=True)
    args=parser.parse_args()
    build(args.output,args.classification_details)


if __name__=='__main__':
    main()

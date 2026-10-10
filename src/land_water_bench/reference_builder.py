"""Build a versioned land/water reference from full GSHHG polygon hierarchies."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import shutil
import struct
import zipfile

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape

from .core import atomic_json, digest

POLICY = "land-water-reference/2.0-gshhg-grounding"
ANCHORS = [
    ("Caspian Sea", 41, 51, 0), ("Lake Superior", 47, -87, 0),
    ("Lake Victoria", -1, 33, 0), ("Paris", 48.8566, 2.3522, 1),
    ("Beijing", 39.9, 116.4, 1), ("Manitoulin Island", 45.7, -82.2, 1),
    ("Pacific east of dateline", 1, -179, 0), ("Pacific west of dateline", 1, 179, 0),
    ("Arctic east", 89, 179, 0), ("Arctic west", 89, -179, 0),
    ("Antarctic interior east", -89, 179, 1), ("Antarctic interior west", -89, -179, 1),
    ("Antarctic interior Greenwich", -89, 1, 1), ("Antarctic interior Indian sector", -89, 91, 1),
]


def even_odd_contains(rings: list[np.ndarray], xy: np.ndarray) -> np.ndarray:
    """Independent horizontal-ray implementation, grouped by latitude for large polygons."""
    result = np.zeros(len(xy), dtype=bool)
    for ring in rings:
        a, b = ring, np.roll(ring, -1, axis=0)
        for latitude in np.unique(xy[:, 1]):
            selected = np.flatnonzero(xy[:, 1] == latitude)
            edges = (a[:, 1] > latitude) != (b[:, 1] > latitude)
            start, end = a[edges], b[edges]
            crossing = start[:, 0] + (latitude - start[:, 1]) * (end[:, 0] - start[:, 0]) / (end[:, 1] - start[:, 1])
            crossing.sort()
            result[selected] ^= (len(crossing) - np.searchsorted(crossing, xy[selected, 0], side="right")) % 2 == 1
    return result


def level_label(level: int) -> int:
    return int(level in {1, 3, 5, 6})


def south_polar(xy: np.ndarray) -> np.ndarray:
    """Azimuthal equidistant coordinates about the south pole, in angular units."""
    radius = 90 + xy[:, 1]
    longitude = np.deg2rad(xy[:, 0])
    return np.column_stack([radius * np.cos(longitude), radius * np.sin(longitude)])


def antarctic_polygons(source: Path):
    # Native files avoid the malformed eastern ice-front closure in the distributed shapefile.
    with (source / "gshhs_f.b").open("rb") as stream:
        while raw := stream.read(44):
            ident, count, flag, *rest = struct.unpack(">11i", raw)
            level = flag & 255
            raw_points = stream.read(count * 8)
            assert len(raw_points) == count * 8
            if level not in {5, 6}:
                continue
            xy = np.frombuffer(raw_points, dtype=">i4").reshape(-1, 2) / 1e6
            info = {"id": str(ident), "level": level, "source": "ANT-I" if level == 5 else "ANT-G",
                    "parent_id": rest[-2], "area": rest[-3] / 10}
            yield level, info, south_polar(xy)


def classify_gshhg(source: Path, xy: np.ndarray):
    points = shapely.points(xy)
    index = shapely.STRtree(points)
    primary = np.zeros(len(points), dtype=np.int8)
    icefront = np.zeros(len(points), dtype=np.int8)
    depths = np.zeros(len(points), dtype=np.int8)
    evidence = [[] for _ in points]
    boundary = np.zeros(len(points), dtype=bool)
    report = []
    checks = 0
    invalid = []
    repairs = []
    antarctic = {5: [], 6: []}
    for level, info, ring in antarctic_polygons(source):
        antarctic[level].append((info, ring))
    polar_xy = south_polar(xy)
    polar_points = shapely.points(polar_xy)
    polar_index = shapely.STRtree(polar_points)
    # Select grounding-line Antarctica for scoring, and retain ice-front labels as a sensitivity check.
    for level in [1, 5, 6, 2, 3, 4]:
        total = candidates = covered = 0
        if level in {5, 6}:
            for info, ring in antarctic[level]:
                total += 1
                geom = shapely.Polygon(ring)
                indices = polar_index.query(shapely.box(*geom.bounds))
                if not len(indices):
                    continue
                candidates += 1
                if not geom.is_valid:
                    issue = shapely.is_valid_reason(geom)
                    original = shapely.covers(geom, polar_points[indices])
                    geom = shapely.make_valid(geom, method="linework")
                    assert geom.is_valid
                    assert np.array_equal(original, shapely.covers(geom, polar_points[indices]))
                    repairs.append({"level": level, "id": info["id"], "reason": issue,
                                    "method": "GEOS make_valid linework", "grid_labels_changed": 0})
                shapely.prepare(geom)
                hit = np.asarray(shapely.covers(geom, polar_points[indices]), dtype=bool)
                independent = even_odd_contains([ring], polar_xy[indices])
                assert np.array_equal(hit, independent), (level, info["id"])
                checks += len(indices)
                selected = indices[hit]
                covered += len(selected)
                if level == 5:
                    icefront[selected] = 1
                else:
                    primary[selected] = 1
                    depths[selected] = 1
                    for i in selected:
                        evidence[i].append({"level": level, "id": info["id"], "source": info["source"],
                                            "parent_id": info["parent_id"], "river_lake": False})
            report.append({"level": level, "format": "native binary, south-polar projection",
                           "features": total, "candidate_features": candidates, "cover_events": covered})
            print(json.dumps(report[-1]), flush=True)
            continue
        with shapefile.Reader(str(source / f"GSHHS_shp/f/GSHHS_f_L{level}.shp")) as reader:
            for item in reader.iterShapeRecords():
                total += 1
                indices = index.query(shapely.box(*item.shape.bbox))
                if not len(indices):
                    continue
                candidates += 1
                geom = shape(item.shape.__geo_interface__)
                info = item.record.as_dict()
                assert int(info["level"]) == level
                if not geom.is_valid:
                    invalid.append({"level": level, "id": info["id"], "reason": shapely.is_valid_reason(geom)})
                    raise ValueError(f"Unexpected invalid non-Antarctic polygon: {info['id']}")
                shapely.prepare(geom)
                hit = np.asarray(shapely.covers(geom, points[indices]), dtype=bool)
                coordinates = np.asarray(item.shape.points, dtype=float)
                parts = list(item.shape.parts) + [len(coordinates)]
                rings = [coordinates[a:b] for a, b in zip(parts, parts[1:])]
                independent = even_odd_contains(rings, xy[indices])
                differences = np.flatnonzero(hit != independent)
                for j in differences:
                    # Inclusion on an exact boundary is the declared tie rule, not an error tolerance in metres.
                    assert geom.boundary.distance(points[indices[j]]) < 1e-9, (level, info["id"], xy[indices[j]])
                    boundary[indices[j]] = True
                checks += len(indices)
                selected = indices[hit]
                covered += len(selected)
                if level != 5:
                    primary[selected] = level_label(level)
                    depths[selected] = 1 if level == 6 else level
                    for i in selected:
                        evidence[i].append({"level": level, "id": info["id"], "source": info["source"],
                                            "parent_id": info["parent_id"], "river_lake": info["area"] < 0})
                if level != 6:
                    icefront[selected] = level_label(level)
                if total % 20000 == 0:
                    print(json.dumps({"level": level, "features_read": total}), flush=True)
        report.append({"level": level, "features": total, "candidate_features": candidates, "cover_events": covered})
        print(json.dumps(report[-1]), flush=True)
    return primary, icefront, depths, evidence, boundary, {"levels": report, "independent_point_polygon_checks": checks,
                                                          "candidate_invalid_geometries": invalid,
                                                          "antarctic_geometry_repairs": repairs}


def classify_natural_earth(source: Path, xy: np.ndarray):
    points = shapely.points(xy)
    index = shapely.STRtree(points)
    land = np.zeros(len(points), dtype=bool)
    water = np.zeros(len(points), dtype=bool)
    names = [[] for _ in points]
    for filename, flags in [("ne_10m_land.geojson", land), ("ne_10m_lakes.geojson", water)]:
        data = json.loads((source / filename).read_text())
        for feature in data["features"]:
            geom = shape(feature["geometry"])
            indices = index.query(shapely.box(*geom.bounds))
            if not len(indices):
                continue
            shapely.prepare(geom)
            selected = indices[shapely.covers(geom, points[indices])]
            flags[selected] = True
            if filename == "ne_10m_lakes.geojson":
                for i in selected:
                    names[i].append(feature["properties"].get("name") or "Unnamed lake")
    return (land & ~water).astype(np.int8), names


def build(source: Path, old_dataset: Path, output: Path):
    assert not (output / "dataset.json").exists(), "Use a new reference directory"
    old = json.loads(old_dataset.read_text())
    manifest = json.loads((source / "manifest.json").read_text())
    for item in manifest["files"]:
        assert digest((source / item["file"]).read_bytes()) == item["sha256"]
    extraction_checks = []
    for archive in ["gshhg-shp-2.3.7.zip", "gshhg-bin-2.3.7.zip"]:
        with zipfile.ZipFile(source / archive) as zipped:
            for name in zipped.namelist():
                if (archive == "gshhg-shp-2.3.7.zip" and name.startswith("GSHHS_shp/f/") and not name.endswith("/")) or name == "gshhs_f.b":
                    checksum = digest((source / name).read_bytes())
                    assert checksum == digest(zipped.read(name))
                    extraction_checks.append({"archive": archive, "file": name, "sha256": checksum})
    protocol = {
        "version": POLICY, "primary_source": "GSHHG 2.3.7 full-resolution coast/lake shapefiles and native binary Antarctic rings",
        "classification": {"ocean": "Water", "lake": "Water", "island_in_lake": "Land",
                           "pond_in_island": "Water", "grounded_ice": "Land", "floating_ice_shelf": "Water",
                           "sea_ice": "Water (underlying ocean)"},
        "antarctica": "L6 grounding line; L5 retained only as an alternative reference",
        "antarctic_geometry": "Native binary rings in south-polar azimuthal equidistant projection; GEOS linework repairs checked for zero grid-label changes and independent ray agreement",
        "boundary_ties": "covered by polygon; deepest nested level wins; exact computational ties flagged",
        "comparison": "Natural Earth 1:10m land and lakes, diagnostic only; never selected using model answers",
        "reference_disagreement_exclusion": False,
        "new_model_requests": 0,
    }
    atomic_json(output / "protocol.json", protocol)
    xy = np.array([[p["lon"], p["lat"]] for p in old["points"]] + [[lon, lat] for _, lat, lon, _ in ANCHORS])
    primary, icefront, depths, evidence, boundary, verification = classify_gshhg(source, xy)
    assert not np.any((primary == 1) & (icefront == 0)), "Grounded land must be inside ice-front land"
    comparison, lake_names = classify_natural_earth(source, xy)
    for i, (name, lat, lon, expected) in enumerate(ANCHORS, start=16200):
        assert int(primary[i]) == expected, (name, primary[i], evidence[i])
        if lat == -89:
            assert int(icefront[i]) == expected, (name, icefront[i])
    anchors = [{"name": name, "lat": lat, "lon": lon, "expected": expected,
                "gshhg": int(primary[16200 + i]), "natural_earth": int(comparison[16200 + i])}
               for i, (name, lat, lon, expected) in enumerate(ANCHORS)]
    dataset = copy.deepcopy(old)
    archive = next(f for f in manifest["files"] if f["file"] == "gshhg-shp-2.3.7.zip")
    dataset["reference"] = {
        "name": "GSHHG", "version": "2.3.7", "policy": POLICY,
        "source": "https://www.soest.hawaii.edu/pwessel/gshhg/",
        "archive_sha256": archive["sha256"], "resolution": "Full-resolution vector polygons; variable source accuracy",
        "antarctic_binary_sha256": next(f["sha256"] for f in manifest["files"] if f["file"] == "gshhg-bin-2.3.7.zip"),
        "published": "2017-06-15", "antarctica": "grounding line",
        "classification": protocol["classification"], "protocol_sha256": digest((output / "protocol.json").read_bytes()),
        "limitation": "Static coast/lake boundaries with older source surveys. Narrow rivers, seasonal water, shoreline changes and boundary positions remain uncertain; not absolute ground truth.",
        "crosscheck": {"source": "Natural Earth 1:10m land and lakes", "commit": manifest["natural_earth_commit"]},
    }
    details = {}
    for i, point in enumerate(dataset["points"]):
        assert point["id"] == i
        previous = point["truth"]
        point["truth"] = int(primary[i])
        category = "land" if primary[i] else "ocean"
        if depths[i] == 2:
            category = "river_lake" if evidence[i][-1]["river_lake"] else "lake"
        elif depths[i] == 3:
            category = "island_in_lake"
        elif depths[i] == 4:
            category = "pond_in_island"
        elif not primary[i] and icefront[i]:
            category = "floating_ice_shelf"
        elif primary[i] and any(e["level"] == 6 for e in evidence[i]):
            category = "grounded_ice"
        details[str(i)] = {"sample_id": i, "lat": point["lat"], "lon": point["lon"],
                           "label": int(primary[i]), "previous_label": previous, "category": category,
                           "features": evidence[i], "exact_boundary_tie": bool(boundary[i]),
                           "natural_earth_label": int(comparison[i]), "lake_names": lake_names[i],
                           "comparison_disagreement": bool(primary[i] != comparison[i]),
                           "icefront_label": int(icefront[i]), "ice_policy_sensitive": bool(primary[i] != icefront[i])}
    changes = [row for row in details.values() if row["label"] != row["previous_label"]]
    disagreements = [row["sample_id"] for row in details.values() if row["comparison_disagreement"]]
    atomic_json(output / "dataset.json", dataset)
    atomic_json(output / "reference.details.json", details)
    atomic_json(output / "changes.json", changes)
    verification.update(passed=True, verified_at=datetime.now(timezone.utc).isoformat(), anchors=anchors,
                        source_extraction_checks=extraction_checks,
                        points=16200, dataset_sha256=digest((output / "dataset.json").read_bytes()),
                        previous_dataset_sha256=digest(old_dataset.read_bytes()),
                        changed_points=len(changes), change_categories=dict(Counter(r["category"] for r in changes)),
                        comparison_disagreement_ids=disagreements,
                        ice_policy_sensitive_ids=[r["sample_id"] for r in details.values() if r["ice_policy_sensitive"]],
                        exact_boundary_ids=[r["sample_id"] for r in details.values() if r["exact_boundary_tie"]],
                        new_model_requests=0, model_answers_used_to_construct_reference=False)
    atomic_json(output / "verification.json", verification)
    atomic_json(output / "build-manifest.json", {"created_at": datetime.now(timezone.utc).isoformat(),
                                                "script_sha256": digest(Path(__file__).read_bytes()),
                                                "old_dataset": str(old_dataset), "source_manifest": manifest,
                                                "dependencies": {n: importlib.metadata.version(n) for n in ["numpy", "shapely", "pyshp"]},
                                                "geos": shapely.geos_version_string})
    shutil.copy2(Path(__file__), output / "reference_builder.py")
    print(json.dumps({"passed": True, "changed_points": len(changes), "change_categories": verification["change_categories"],
                      "comparison_disagreements": len(disagreements), "ice_policy_sensitive": len(verification["ice_policy_sensitive_ids"]),
                      "anchors": anchors}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--old-dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source_dir, args.old_dataset, args.output)

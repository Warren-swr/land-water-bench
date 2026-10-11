"""Coordinate-only terrain classification and elevation pilot, using the shared transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
from pathlib import Path

from .core import digest, extract_answer, response_is_filtered
from .runner import Runner, SOURCE_FILES

PROTOCOL = "terrain-elevation/0.1-stratified-pilot"
LEGACY_PARSER_VERSION = "terrain-answer/1.0-single-json"
PARSER_VERSION = "terrain-answer/2.0-final-json"
SCORE_VERSION = "terrain-score/1.0-joint-tolerance"
TOLERANCES = (100, 500, 1000)
PROMPT = (
    "Latitude: {lat:.1f}, Longitude: {lon:.1f}. "
    "Predict the surface type (Solid or Water) and terrain elevation in meters relative to mean sea level "
    "(EGM2008). For Solid, report bare-ground elevation. For Water, report the seabed or lakebed elevation, "
    "NOT the water-surface elevation; underwater terrain below sea level has a negative elevation. "
    "Ignore buildings, vegetation, and temporary sea ice. "
    'Reply with exactly one JSON object with two fields: "type" ("Solid" or "Water") '
    'and "elevation_m" (a single number in meters, not a range).'
)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_legacy_prediction(text: str) -> tuple[dict | None, str]:
    """Accept one complete answer object in the body, including fences or explanatory text."""
    decoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    candidates = []
    offset = 0
    while (start := text.find("{", offset)) != -1:
        try:
            obj, length = decoder.raw_decode(text[start:])
        except (ValueError, RecursionError):
            # Do not salvage an inner object from a malformed answer.
            return None, "malformed_json"
        offset = start + length
        if isinstance(obj, dict) and ("type" in obj or "elevation_m" in obj):
            candidates.append(obj)
    if len(candidates) != 1:
        return None, "missing_or_multiple_answers"
    obj = candidates[0]
    if set(obj) != {"type", "elevation_m"}:
        return None, "wrong_fields"
    kind, elevation = obj["type"], obj["elevation_m"]
    if not isinstance(kind, str) or kind.casefold() not in {"solid", "water"}:
        return None, "invalid_type"
    if type(elevation) not in (int, float) or not math.isfinite(elevation):
        return None, "invalid_elevation"
    return {"type": kind.capitalize(), "elevation_m": elevation}, "single_json_object"


def object_end(text: str, start: int) -> int:
    """Skip a whole brace group, including a malformed object, without salvaging its children."""
    depth = 0
    quoted = escaped = False
    for offset in range(start, len(text)):
        char = text[offset]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return offset + 1
    return len(text)


REVISION = re.compile(
    r"(?:^|\n|[—–]|(?<=[.!?])\s+)\s*(?:correction\s*:|corrected(?:,\s*valid\s+json|\s+answer)?\s*:"
    r"|let\s+me\s+(?:refine|correct)\s*:|i\s+need\s+to\s+correct\s+that\s*:"
    r"|revised\s+answer\s*:|(?:alright,\s*)?final\s+answer[.:]"
    r"|(?:更正|修正)(?:后的答案)?\s*[:：])", re.I)


def parse_prediction(text: str, *, version: str = PARSER_VERSION) -> tuple[dict | None, str]:
    """Read strict JSON answers, allowing repeated answers and explicit final corrections."""
    if version == LEGACY_PARSER_VERSION:
        return _parse_legacy_prediction(text)
    if version != PARSER_VERSION:
        raise ValueError("Unsupported terrain answer parser")
    decoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    events = []
    offset = 0
    while (start := text.find("{", offset)) != -1:
        try:
            obj, length = decoder.raw_decode(text[start:])
        except (ValueError, RecursionError):
            offset = object_end(text, start)
            block = text[start:offset]
            # Math groups and template placeholders are not attempted JSON answers.
            if re.match(r"\{\s*[\"']", block) or re.search(r"\b(?:type|elevation_m)\s*[\"']?\s*:", block):
                events.append((start, offset, None, "malformed_json"))
            continue
        offset = start + length
        if not isinstance(obj, dict) or not {"type", "elevation_m"}.intersection(obj):
            continue
        prediction, rule = _parse_legacy_prediction(text[start:offset])
        events.append((start, offset, prediction, rule))
    if not events:
        return None, "missing_or_multiple_answers"
    final = events[-1]
    if final[2] is None:
        return None, final[3]
    if len(events) == 1:
        return final[2], "single_json_object"
    if all(event[2] == final[2] for event in events):
        return final[2], "repeated_identical_json"
    bridge = text[events[-2][1]:final[0]]
    if REVISION.search(bridge):
        return final[2], "explicit_final_correction"
    return None, "missing_or_multiple_answers"


def project_answer(row: dict, wire: str, *, version: str = PARSER_VERSION) -> dict:
    text, complete = extract_answer(wire, row["response"])
    if version == PARSER_VERSION and response_is_filtered(row["response"]):
        return {"text": text, "complete_response": False, "status": "infra_error", "label": None,
                "prediction": None, "error": {"type": "content_filter"},
                "extraction": {"version": version, "rule": "content_filter"}}
    prediction, rule = parse_prediction(text, version=version) if complete else (None, "incomplete_response")
    return {"text": text, "complete_response": complete,
            "status": "ok" if prediction else "invalid",
            "label": int(prediction["type"] == "Solid") if prediction else None,
            "prediction": prediction, "extraction": {"version": version, "rule": rule}}


def score(points: list[dict], records: dict[int, dict]) -> dict:
    unresolved = invalid = valid = classification_correct = 0
    errors = []
    joint = dict.fromkeys(TOLERANCES, 0)
    for point in points:
        row = records.get(point["id"])
        if row is None:
            continue
        if row["status"] == "infra_error":
            unresolved += 1
            continue
        if row["status"] != "ok":
            invalid += 1
            continue
        valid += 1
        error = abs(row["prediction"]["elevation_m"] - point["elevation_m"])
        errors.append(error)
        correct = row["label"] == point["truth"]
        classification_correct += correct
        for tolerance in TOLERANCES:
            joint[tolerance] += bool(correct and error <= tolerance)
    pending = sum(p["id"] not in records for p in points)
    scored = valid + invalid
    finished = pending == 0
    return {"expected": len(points), "valid": valid, "invalid": invalid,
            "infra_errors": unresolved, "pending": pending, "scored": scored,
            "complete": finished, "coverage": scored / len(points) if points else None,
            "numeric_coverage": valid / scored if scored else None,
            "classification_correct": classification_correct,
            "classification_accuracy": classification_correct / scored if finished and scored else None,
            "observed_classification_accuracy": classification_correct / scored if scored else None,
            "joint_correct": {str(t): joint[t] for t in TOLERANCES},
            "joint_accuracy": {str(t): joint[t] / scored if finished and scored else None for t in TOLERANCES},
            "elevation_mae_m": statistics.fmean(errors) if errors else None,
            "elevation_median_ae_m": statistics.median(errors) if errors else None,
            "elevation_p90_ae_m": sorted(errors)[min(len(errors)-1, math.ceil(len(errors)*.9)-1)] if errors else None}


def metrics(points, records):
    result = score(points, records)
    result["by_type"] = {name: score([p for p in points if p["truth"] == label], records)
                         for name, label in [("Solid", 1), ("Water", 0)]}
    result["by_stratum"] = {name: score([p for p in points if p["stratum"] == name], records)
                            for name in sorted({p["stratum"] for p in points})}
    return result


def validate_dataset(dataset):
    points = dataset["points"]
    if dataset.get("protocol") != PROTOCOL or not points:
        raise ValueError("Expected a terrain pilot dataset")
    if [p["id"] for p in points] != list(range(len(points))):
        raise ValueError("Terrain IDs must be contiguous and unique")
    if len({(p["lat"], p["lon"]) for p in points}) != len(points):
        raise ValueError("Duplicate coordinate")
    for point in points:
        if point["truth"] not in (0, 1) or not math.isfinite(point["elevation_m"]):
            raise ValueError("Invalid terrain reference")
        if point["weight"] != 1 or point["prompt"] != PROMPT.format(**point):
            raise ValueError("Pilot uses fixed prompts and equal weights")
    return dataset


class TerrainRunner(Runner):
    protocol = PROTOCOL
    answer_version = PARSER_VERSION
    score_version = SCORE_VERSION
    source_url = "https://www.ncei.noaa.gov/products/etopo-global-relief-model"
    source_files = SOURCE_FILES + ("terrain.py",)

    def make_dataset(self):
        return validate_dataset(json.loads(self.args.dataset.read_text()))

    def config_updates(self):
        return {"input_dataset_sha256": digest(self.args.dataset.read_bytes()),
                "primary_metric": "joint_accuracy.500", "tolerances_m": list(TOLERANCES)}

    def project_answer(self, row, wire):
        return project_answer(row, wire)

    def score_records(self, points, records):
        return metrics(points, records)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--models", nargs="+", required=True)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--export-dir", type=Path)
    p.add_argument("--concurrency", type=int, default=512)
    p.add_argument("--max-retries", type=int, default=1000)
    p.add_argument("--max-timeout-retries", type=int, default=2)
    p.add_argument("--timeout", type=float, default=90)
    p.add_argument("--seed", type=int, default=20261010)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--max-output-tokens", type=int, default=65536)
    p.add_argument("--resume-revision")
    args = p.parse_args(argv)
    if not 1 <= args.concurrency <= 512 or not 0 <= args.max_retries <= 1000:
        p.error("concurrency must be 1..512; max-retries must be 0..1000")
    if args.max_timeout_retries < 0 or args.timeout <= 0 or args.limit < 0 or args.max_output_tokens < 1:
        p.error("Invalid timeout, retry limit, sample limit, or output cap")
    args.export_dir = args.export_dir or args.run_dir / "snapshot"
    asyncio.run(TerrainRunner(args).execute())


if __name__ == "__main__":
    main()

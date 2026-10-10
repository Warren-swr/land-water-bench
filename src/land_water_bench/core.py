"""Frozen coordinate protocol, strict answer parsing, and area-weighted scoring."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

PROTOCOL = "land-water/1.0-center-2deg"
PROMPT = (
    "Latitude: {lat:.1f}, Longitude: {lon:.1f}. Land or Water? "
    "Reply with exactly one word: Land or Water."
)
SOURCE_URL = "https://x.com/celestepoasts/status/2103232383139057950"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    temp.replace(path)


def make_grid() -> list[dict]:
    return [
        {"id": row * 180 + col, "row": row, "col": col,
         "lat": 89 - 2 * row, "lon": -179 + 2 * col,
         "weight": math.cos(math.radians(89 - 2 * row))}
        for row in range(90) for col in range(180)
    ]


def make_dataset() -> dict:
    """Load the frozen GSHHG reference shipped with the package (no network access)."""
    from importlib.resources import files

    return json.loads(files("land_water_bench").joinpath("data/dataset.json").read_text())


def parse_label(text: str) -> int | None:
    # Do not search for a convenient label inside reasoning or ambiguous prose.
    answer = text.strip()
    if answer.lower() == "land":
        return 1
    if answer.lower() == "water":
        return 0
    return None


def extract_answer(wire: str, body: dict) -> tuple[str, bool]:
    if wire == "responses":
        messages = [x for x in body.get("output", []) if x.get("type") == "message"]
        text = "".join(c.get("text", "") for msg in messages
                       for c in msg.get("content", []) if c.get("type") == "output_text")
        allowed = {"message", "reasoning"}
        complete = (body.get("status") == "completed" and not body.get("error")
                    and not body.get("incomplete_details")
                    and all(x.get("type") in allowed for x in body.get("output", [])))
        return text, complete
    text = "".join(c.get("text", "") for c in body.get("content", [])
                   if c.get("type") == "text")
    return text, body.get("stop_reason") == "end_turn" and not body.get("error")


def usage_counts(usage: dict) -> dict:
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "reasoning_tokens": usage.get("output_tokens_details", {}).get("reasoning_tokens", 0),
    }


def metrics(points: list[dict], records: dict[int, dict]) -> dict:
    total_weight = sum(p["weight"] for p in points)
    correct_weight = observed_weight = valid_weight = wrong_weight = 0.0
    correct = valid = invalid = infra = 0
    confusion = {"land_land": 0, "land_water": 0, "water_land": 0, "water_water": 0}
    weighted_confusion = dict.fromkeys(confusion, 0.0)
    for p in points:
        r = records.get(p["id"])
        if r is None:
            continue
        if r["status"] == "infra_error":
            infra += 1
            continue
        observed_weight += p["weight"]
        if r["status"] != "ok":
            invalid += 1
            wrong_weight += p["weight"]
            continue
        valid += 1
        valid_weight += p["weight"]
        key = ("land" if p["truth"] else "water") + "_" + ("land" if r["label"] else "water")
        confusion[key] += 1
        weighted_confusion[key] += p["weight"]
        if r["label"] == p["truth"]:
            correct += 1
            correct_weight += p["weight"]
        else:
            wrong_weight += p["weight"]
    complete = valid + invalid == len(points)
    land_weight = weighted_confusion["land_land"] + weighted_confusion["land_water"]
    water_weight = weighted_confusion["water_land"] + weighted_confusion["water_water"]
    return {
        "expected": len(points), "valid": valid, "invalid": invalid, "infra_errors": infra,
        "pending": len(points) - len(records), "correct": correct,
        "coverage": (valid + invalid) / len(points),
        "area_coverage": observed_weight / total_weight,
        "complete": complete,
        "weighted_accuracy": correct_weight / total_weight if complete else None,
        "observed_weighted_accuracy": correct_weight / observed_weight if observed_weight else None,
        "valid_weighted_accuracy": correct_weight / valid_weight if valid_weight else None,
        "point_accuracy": correct / len(points) if complete else None,
        "accuracy_lower_bound": correct_weight / total_weight,
        "accuracy_upper_bound": 1 - wrong_weight / total_weight,
        "all_water_accuracy": sum(p["weight"] for p in points if not p["truth"]) / total_weight,
        "land_recall": weighted_confusion["land_land"] / land_weight if land_weight else None,
        "water_recall": weighted_confusion["water_water"] / water_weight if water_weight else None,
        "confusion": confusion,
    }


def read_jsonl(path: Path, *, recover_tail: bool = False) -> list[dict]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    records = []
    offset = 0
    for i, line in enumerate(raw.splitlines(keepends=True)):
        try:
            records.append(json.loads(line))
        except (ValueError, UnicodeDecodeError):
            if not recover_tail or offset + len(line) != len(raw) or line.endswith(b"\n"):
                raise ValueError(f"Corrupt evidence in {path.name}, line {i + 1}") from None
            # Preserve the truncated write before repairing only its uncommitted tail.
            path.with_name(path.name + f".tail-{digest(line)[:12]}").write_bytes(line)
            with path.open("r+b") as f:
                f.truncate(offset)
        offset += len(line)
    return records

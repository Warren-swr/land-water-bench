"""Frozen provenance for replaying only responses stopped by the output cap."""

from __future__ import annotations

import json
from pathlib import Path

from .core import digest, read_jsonl


def output_truncated(record: dict) -> bool:
    response = record.get("response") or {}
    return record.get("status") == "invalid" and (
        response.get("stop_reason") == "max_tokens"
        or (response.get("incomplete_details") or {}).get("reason") == "max_output_tokens")


def make_plan(source: Path, model: dict) -> dict:
    source = source.resolve()
    config = json.loads((source / "config.json").read_text())
    if config.get("replay"):
        raise ValueError("Choose an original run as the replay source")
    original = next(m for m in config["models"] if m["name"] == model["name"])
    if {k: v for k, v in original.items() if k != "max_output_tokens"} != {
        k: v for k, v in model.items() if k != "max_output_tokens"
    }:
        raise ValueError("Replay may change only the output cap, not model or reasoning settings")
    if model["max_output_tokens"] <= original["max_output_tokens"]:
        raise ValueError("Replay output cap must exceed the original cap")
    summary = json.loads((source / "summary.json").read_text())
    metric = next(m["metrics"] for m in summary["models"] if m["name"] == model["name"])
    if metric["pending"] or config["limit"]:
        raise ValueError("Replay requires a terminal full-grid source")
    proof = json.loads((source / "verification.json").read_text())
    if not proof.get("passed"):
        raise ValueError("Replay source has not passed verification")
    name = model["name"] + ".jsonl"
    hashes = {name: digest((source / name).read_bytes()) for name in (
        "config.json", "manifest.json", "dataset.json", "verification.json", name)}
    seal = dict(line.split("  ", 1)[::-1] for line in (source / "FILES.sha256").read_text().splitlines())
    if any(seal.get(name) != sha for name, sha in hashes.items()):
        raise ValueError("Replay source differs from its sealed evidence")
    records = read_jsonl(source / name)
    selected = sorted(r["sample_id"] for r in records if output_truncated(r))
    if not selected:
        raise ValueError("Source contains no output-cap truncations")
    return {"source_run": str(source), "source_sha256": hashes, "model": model["name"],
            "sample_ids": selected, "source_max_output_tokens": original["max_output_tokens"],
            "retained_non_truncated_invalid": sum(r["status"] == "invalid" and not output_truncated(r) for r in records)}


def source_records(plan: dict) -> list[dict]:
    source = Path(plan["source_run"])
    for name, expected in plan["source_sha256"].items():
        if digest((source / name).read_bytes()) != expected:
            raise ValueError(f"Replay source checksum mismatch: {name}")
    records = read_jsonl(source / (plan["model"] + ".jsonl"))
    if sorted(r["sample_id"] for r in records if output_truncated(r)) != plan["sample_ids"]:
        raise ValueError("Replay selection differs from the original output-cap truncations")
    return records


def effective_records(run: Path, name: str) -> list[dict]:
    config = json.loads((run / "config.json").read_text())
    fresh = read_jsonl(run / (name + ".jsonl"))
    plan = config.get("replay")
    if not plan:
        return fresh
    if plan["model"] != name:
        raise ValueError("Replay model mismatch")
    selected = set(plan["sample_ids"])
    if any(r["sample_id"] not in selected for r in fresh):
        raise ValueError("Replay contains a request outside the frozen selection")
    return [r for r in source_records(plan) if r["sample_id"] not in selected] + fresh

"""Branch a sealed run to resume only unresolved points with additional timeout retries."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .core import atomic_json, digest
from .results import read, require


def prepare_branch(source: Path, output: Path, additional_timeout_retries=5):
    source = source.resolve()
    require(not output.exists(), "Recovery needs a fresh run directory")
    require(1 <= additional_timeout_retries <= 20, "Additional timeout retries must be 1..20")
    checksums = {}
    for line in (source / "FILES.sha256").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        checksums[name] = checksum

    def checked(name):
        data = (source / name).read_bytes()
        require(digest(data) == checksums.get(name), "Source checksum mismatch: " + name)
        return data

    config = json.loads(checked("config.json"))
    manifest = json.loads(checked("manifest.json"))
    summary = json.loads(checked("summary.json"))
    proof = json.loads(checked("global-verification.json"))
    require(proof["passed"] and summary["state"] == "complete", "Source is not verified and complete")
    require(len(config["models"]) == 1 and not config["limit"], "Expected one full-grid model")
    require(config["protocol"] == "terrain-elevation/1.0-global-center-2deg", "Expected global terrain protocol")
    model = config["models"][0]["name"]
    # No endpoint or reasoning change is allowed by the resumed runner's configuration guard.
    source_root = Path(__file__).resolve().parent
    for name, checksum in manifest["execution_source_sha256"].items():
        require(digest((source_root / name).read_bytes()) == checksum, "Execution code changed: " + name)
    output.mkdir(parents=True)
    selected, kept, seen = [], 0, set()
    name = model + ".jsonl"
    hasher = hashlib.sha256()
    with (source / name).open("rb") as original, (output / name).open("xb") as retained:
        for line in original:
            hasher.update(line)
            # Parse every terminal row to check selection, without changing retained bytes.
            row = json.loads(line)
            require(row["sample_id"] not in seen, "Duplicate source record")
            seen.add(row["sample_id"])
            if row["status"] == "infra_error":
                selected.append(row["sample_id"])
            else:
                retained.write(line)
                kept += 1
    require(hasher.hexdigest() == checksums[name], "Source response checksum mismatch")
    require(seen == set(range(16200)), "Source is not the full grid")
    require(len(selected) == summary["models"][0]["metrics"]["infra_errors"] and selected,
            "Unresolved selection does not match source summary")

    def copy_checked(name):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        with (source / name).open("rb") as original, target.open("xb") as copied:
            for block in iter(lambda: original.read(1024 * 1024), b""):
                hasher.update(block)
                copied.write(block)
        require(hasher.hexdigest() == checksums.get(name), "Source checksum mismatch: " + name)

    copy_checked("dataset.json")
    copy_checked("attempts.jsonl")
    for name in manifest["execution_source_sha256"]:
        copy_checked("source/" + name)
    previous_budget = config["max_timeout_retries"]
    config["max_timeout_retries"] += additional_timeout_retries
    now = datetime.now(timezone.utc).isoformat()
    receipt = {"source_run": str(source), "created_at": now, "model": model,
               "source_seal_sha256": digest((source / "FILES.sha256").read_bytes()),
               "source_files_sha256": {n: checksums[n] for n in
                    ("config.json", "manifest.json", "summary.json", "dataset.json", "attempts.jsonl",
                     "global-verification.json", model + ".jsonl")},
               "sample_ids": sorted(selected), "retained_records": kept,
               "additional_timeout_retries": additional_timeout_retries,
               "previous_max_timeout_retries": previous_budget,
               "max_timeout_retries": config["max_timeout_retries"],
               "prior_attempts_and_timeout_counts_preserved": True,
               "ordinary_invalid_answers_retried": False,
               "cumulative_attempt_limit": config["max_retries"] + 1}
    manifest["parent_created_at"] = manifest["created_at"]
    manifest["created_at"] = now
    manifest["run_id"] = output.name
    manifest["recovery"] = receipt
    atomic_json(output / "config.json", config)
    atomic_json(output / "manifest.json", manifest)
    atomic_json(output / "RECOVERY.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--additional-timeout-retries", type=int, default=5)
    args = parser.parse_args()
    result = prepare_branch(args.source, args.output, args.additional_timeout_retries)
    print(json.dumps({k: v for k, v in result.items() if k not in ("sample_ids", "source_files_sha256")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

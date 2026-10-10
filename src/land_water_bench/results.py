"""Offline score verification, public result projection, and static site assembly."""

from __future__ import annotations

import copy
import fcntl
import json
import math
import random
import shutil
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from .answers import VERSION as ANSWER_VERSION, parse_final_answer, reextract_record
from .config import ModelConfig, safe_name
from .core import PROMPT, atomic_json, digest, make_dataset, make_grid, metrics
from .replay import effective_records
from .scoring import VERSION as SCORE_VERSION, exclude_unresolved


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path: Path):
    return json.loads(path.read_text())


def scores_agree(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(scores_agree(left[k], right[k]) for k in left)
    if isinstance(left, float) and isinstance(right, (float, int)):
        return math.isclose(left, right, rel_tol=0, abs_tol=1e-12)
    return left == right


def rescore(points: list[dict], records: dict[int, dict]) -> tuple[dict, dict]:
    full = metrics(points, records)
    score = exclude_unresolved(full)
    # Compute the primary metric independently, without using stored weights.
    included = [p for p in points if p["id"] in records and records[p["id"]]["status"] != "infra_error"]
    numerator = math.fsum(math.cos(math.radians(p["lat"])) for p in included
                          if records[p["id"]]["status"] == "ok" and records[p["id"]]["label"] == p["truth"])
    denominator = math.fsum(math.cos(math.radians(p["lat"])) for p in included)
    if score["complete"]:
        require(math.isclose(score["weighted_accuracy"], numerator / denominator, rel_tol=0, abs_tol=1e-12),
                "Independent score disagrees")
    return score, full


def seal(directory: Path):
    paths = sorted(p for p in directory.rglob("*") if p.is_file() and p.name not in {"FILES.sha256", ".runner.lock"})
    require(not any(p.is_symlink() for p in paths), "Do not seal symlinks")
    (directory / "FILES.sha256").write_text("".join(
        f"{digest(p.read_bytes())}  {p.relative_to(directory).as_posix()}\n" for p in paths))


def verify_seal(directory: Path):
    root = directory.resolve()
    listed = {}
    for line in (root / "FILES.sha256").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        path = (root / name).resolve()
        require(root in path.parents and name not in listed and len(checksum) == 64, "Invalid checksum manifest")
        require(path.is_file() and digest(path.read_bytes()) == checksum, f"Checksum mismatch: {name}")
        listed[name] = checksum
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
              if p.is_file() and p.name not in {"FILES.sha256", ".runner.lock"}}
    require(actual == listed.keys(), "Checksum manifest has missing or extra files")
    return listed


def validate_dataset(dataset):
    grid = make_grid()
    require(len(dataset["points"]) == len(grid), "Reference grid size changed")
    for point, expected in zip(dataset["points"], grid):
        require(all(point[k] == v for k, v in expected.items()), "Coordinate or area weight changed")
        require(point["prompt"] == PROMPT.format(**expected) and point["truth"] in (0, 1), "Invalid prompt or reference label")


def pixels(records, count=16200):
    return "".join(str(records[i]["label"]) if records[i]["status"] == "ok"
                   else "3" if records[i]["status"] == "infra_error" else "2" for i in range(count))


def verify_results(directory: Path, *, check_seal=True) -> dict:
    if check_seal:
        verify_seal(directory)
    data = read(directory / "data.json")
    provenance = read(directory / "provenance.json")
    dataset = make_dataset()
    validate_dataset(dataset)
    reference_bytes = files("land_water_bench").joinpath("data/dataset.json").read_bytes()
    require(data["dataset_sha256"] == digest(reference_bytes), "Result uses a different reference")
    require(data["truth"] == "".join(str(p["truth"]) for p in dataset["points"]), "Reference pixels changed")
    require(data["reference"] == dataset["reference"], "Reference metadata changed")
    require(provenance["answer_parser"] == ANSWER_VERSION and provenance["score_policy"] == SCORE_VERSION,
            "Unsupported parsing/scoring version")
    reference = read(directory / "reference.details.json")
    require(set(reference) == {str(i) for i in range(16200)}, "Reference detail grid is incomplete")
    for i, point in enumerate(dataset["points"]):
        require(reference[str(i)]["sample_id"] == i and reference[str(i)]["label"] == point["truth"], "Reference detail mismatch")
    disagreement = [i for i in range(16200) if reference[str(i)]["comparison_disagreement"]]
    require(disagreement == data["reference_disagreement_ids"], "Reference disagreement markers changed")
    checked = []
    names = set()
    for model in data["models"]:
        name = model["name"]
        require(safe_name(name) and name not in names and name != "reference", "Duplicate or unsafe model name")
        names.add(name)
        require(model["details_url"] == name + ".details.json", "Unsafe result path")
        raw = read(directory / model["details_url"])
        require(set(raw) == {str(i) for i in range(16200)}, f"Incomplete result: {name}")
        records = {int(k): v for k, v in raw.items()}
        incomplete = set(provenance["models"][name]["incomplete_response_ids"])
        for i, row in records.items():
            require(row["sample_id"] == i and row["status"] in {"ok", "invalid", "infra_error"}, "Invalid record identity/status")
            require(row["label"] in (0, 1) if row["status"] == "ok" else row["label"] is None, "Invalid prediction label")
            if row["status"] != "infra_error":
                require(isinstance(row["text"], str), "Missing answer body")
                parsed = None if i in incomplete else parse_final_answer(row["text"])["label"]
                require(parsed == row["label"] and (parsed is not None) == (row["status"] == "ok"),
                        f"Answer extraction mismatch: {name}, sample {i}")
        require(all(i in records and records[i]["status"] == "invalid" for i in incomplete), "Invalid incomplete-response metadata")
        score, _ = rescore(dataset["points"], records)
        require(score["weighted_accuracy"] is not None, f"No scored coordinates: {name}")
        require(scores_agree(score, model["metrics"]), f"Stored metrics disagree: {name}")
        require(pixels(records) == model["pixels"], f"Map predictions disagree: {name}")
        require(score["pending"] == 0, "Cannot publish pending results")
        checked.append({"name": name, "records": len(records), "accuracy": score["weighted_accuracy"],
                        "invalid": score["invalid"], "excluded_unresolved": score["excluded_unresolved"]})
    require(names and names == provenance["models"].keys(), "Provenance model set differs")
    expected = sorted(data["models"], key=lambda m: (-m["metrics"]["weighted_accuracy"], m["name"]))
    require(data["models"] == expected, "Leaderboard must be sorted by accuracy")
    return {"passed": True, "models": checked, "records": sum(m["records"] for m in checked),
            "reference_sha256": data["dataset_sha256"], "new_api_requests": 0,
            "answer_extraction_verified": True, "independent_score_verified": True}


def verify_run(run: Path, *, write=False) -> dict:
    """Verify terminal records and requests offline; refuse an actively running directory."""
    with (run / ".runner.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config, manifest, summary = [read(run / name) for name in ("config.json", "manifest.json", "summary.json")]
        dataset = read(run / "dataset.json")
        validate_dataset(dataset)
        require(digest((run / "dataset.json").read_bytes()) == manifest["dataset_sha256"], "Frozen reference changed")
        require(config == summary["config"] and manifest["dataset_sha256"] == summary["dataset_sha256"], "Run snapshot differs")
        for name, checksum in manifest["execution_source_sha256"].items():
            require(digest((run / "source" / name).read_bytes()) == checksum, "Execution source changed")
        for revision in manifest.get("execution_revisions", []):
            for name, checksum in revision["sha256"].items():
                require(digest((run / revision["source_directory"] / name).read_bytes()) == checksum, "Execution revision changed")
        ids = list(range(16200))
        random.Random(config["shuffle_seed"]).shuffle(ids)
        if config["limit"]:
            ids = ids[:config["limit"]]
        points = [dataset["points"][i] for i in ids]
        checks = []
        for model in config["models"]:
            rows = effective_records(run, model["name"])
            records = {row["sample_id"]: row for row in rows}
            require(len(records) == len(rows) and set(records) == set(ids), "Run is pending or contains duplicate/out-of-grid samples")
            connection = ModelConfig(model["name"], model["requested_model"], model["wire"], "", "", model["reasoning"], model["max_output_tokens"])
            replay = config.get("replay")
            for row in rows:
                _, request = connection.request(dataset["points"][row["sample_id"]]["prompt"])
                if replay and row["sample_id"] not in replay["sample_ids"]:
                    cap_field = "max_output_tokens" if model["wire"] == "responses" else "max_tokens"
                    request[cap_field] = replay["source_max_output_tokens"]
                require(row["request"] == request, "Request prompt/settings changed or includes extra context")
                parsed = reextract_record(row, model["wire"])
                require((parsed["status"], parsed["label"]) == (row["status"], row.get("label")), "Stored answer differs from API body")
            score, _ = rescore(points, records)
            saved = next(m for m in summary["models"] if m["name"] == model["name"])
            require(scores_agree(saved["metrics"], score), "Run score mismatch")
            checks.append({"name": model["name"], "records": len(records), "invalid": score["invalid"],
                           "excluded_unresolved": score["excluded_unresolved"], "accuracy": score["weighted_accuracy"]})
        proof = {"passed": True, "run_id": run.name, "models": checks, "full_grid": not bool(config["limit"]),
                 "new_api_requests": 0, "score_policy": SCORE_VERSION, "answer_parser": ANSWER_VERSION}
        if write:
            require(not (run / "FILES.sha256").exists(), "Run is already sealed; use verify-run without --seal")
            atomic_json(run / "verification.json", proof)
            seal(run)
        elif (run / "FILES.sha256").exists():
            verify_seal(run)
        return proof


def result_table(data):
    lines = ["# Land / Water Bench results", "", "Area-weighted accuracy: invalid answers count as wrong; unresolved API requests are excluded.", "",
             "| Rank | Model | Accuracy | Scored | Invalid | Unresolved |",
             "| ---: | --- | ---: | ---: | ---: | ---: |"]
    for i, model in enumerate(data["models"], 1):
        m = model["metrics"]
        lines.append(f"| {i} | {model['display_name']} | {m['weighted_accuracy']:.4%} | {m['scored_points']:,} | {m['invalid']} | {m['excluded_unresolved']} |")
    lines += ["", "The settings and output caps differ between models. These are single-sample results, not a matched-compute comparison.",
              "Model names are experiment aliases reported by the configured endpoints; backend identity is not independently attested.",
              "GSHHG 2.3.7 is a static reference, not absolute ground truth. Natural Earth disagreement points remain scored.", ""]
    return "\n".join(lines)


def export_run(run: Path, output: Path, base: Path | None = None):
    proof = verify_run(run)
    require(proof["full_grid"], "Smoke runs cannot enter the leaderboard")
    require(not output.exists(), "Use a fresh result directory")
    summary = read(run / "summary.json")
    require(summary["dataset_sha256"] == digest(files("land_water_bench").joinpath("data/dataset.json").read_bytes()),
            "Run must use the packaged reference")
    if base:
        verify_results(base)
        data, provenance = copy.deepcopy(read(base / "data.json")), copy.deepcopy(read(base / "provenance.json"))
    else:
        dataset = make_dataset()
        reference = json.loads(files("land_water_bench").joinpath("data/reference.details.json").read_text())
        data = {"models": [], "dataset_sha256": summary["dataset_sha256"], "truth": summary["truth"],
                "reference": dataset["reference"],
                "reference_disagreement_ids": [int(k) for k, row in reference.items() if row["comparison_disagreement"]]}
        provenance = {"answer_parser": ANSWER_VERSION, "score_policy": SCORE_VERSION, "models": {}}
    names = {m["name"] for m in data["models"]}
    require(not names.intersection(m["name"] for m in summary["models"]), "Duplicate model alias; use a new alias for a new experiment")
    output.mkdir(parents=True)
    if base:
        for model in data["models"]:
            shutil.copy2(base / model["details_url"], output / model["details_url"])
        shutil.copy2(base / "reference.details.json", output / "reference.details.json")
    else:
        atomic_json(output / "reference.details.json", reference)
    for model in summary["models"]:
        name = model["name"]
        records = {row["sample_id"]: row for row in effective_records(run, name)}
        details = {str(i): {"sample_id": i, "status": row["status"], "label": row.get("label"), "text": row.get("text")}
                   for i, row in sorted(records.items())}
        family = "GPT" if name.startswith("gpt-") else "Claude" if name.startswith("claude-") else "Other"
        score, _ = rescore(make_dataset()["points"], records)
        require(score["weighted_accuracy"] is not None, f"No scored coordinates: {name}")
        data["models"].append({"name": name, "display_name": name, "family": family, "symbol": name[0].upper(),
                               "metrics": score, "pixels": pixels(records), "details_url": name + ".details.json",
                               "settings": f"{json.dumps(model['reasoning'])} · max output: {model['max_output_tokens']:,}"})
        provenance["models"][name] = {"wire": model["wire"], "reasoning": model["reasoning"],
                                      "max_output_tokens": model["max_output_tokens"],
                                      "concurrency": summary["config"]["concurrency"], "concurrency_scope": "per model",
                                      "incomplete_response_ids": [i for i, row in records.items() if row.get("extraction", {}).get("reason") == "incomplete_response"],
                                      "replayed_truncation_ids": summary["config"].get("replay", {}).get("sample_ids", []),
                                      "source_records_sha256": digest((run / (name + ".jsonl")).read_bytes())}
        atomic_json(output / (name + ".details.json"), details)
    data.update(run_id=output.name, updated_at=datetime.now(timezone.utc).isoformat())
    data["models"].sort(key=lambda m: (-m["metrics"]["weighted_accuracy"], m["name"]))
    atomic_json(output / "data.json", data)
    atomic_json(output / "provenance.json", provenance)
    atomic_json(output / "verification.json", verify_results(output, check_seal=False))
    (output / "RESULTS.md").write_text(result_table(data))
    seal(output)
    return verify_results(output)


def build_site(results: Path, output: Path, assets: Path):
    proof = verify_results(results)
    require(not output.exists(), "Use a fresh site output directory (remove only your previous build)")
    data = read(results / "data.json")
    output.mkdir(parents=True)
    for name in ["index.html", "app.js", "style.css", "display.ttf", "FONT-LICENSE.txt"]:
        shutil.copy2(assets / name, output / name)
    for name in ["data.json", "reference.details.json"] + [m["details_url"] for m in data["models"]]:
        shutil.copy2(results / name, output / name)
    (output / "RESULTS.md").write_text(result_table(data))
    (output / ".nojekyll").touch()
    root = assets.parent
    for source, name in [(root / "LICENSE", "LICENSE"), (root / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
                         (root / "licenses/LGPL-3.0.txt", "GSHHG-LICENSE.txt"), (root / "licenses/GPL-3.0.txt", "GSHHG-GPL.txt")]:
        shutil.copy2(source, output / name)
    return {"passed": True, "models": len(data["models"]), "records": proof["records"], "output": str(output)}

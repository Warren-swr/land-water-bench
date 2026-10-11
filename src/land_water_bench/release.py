"""Self-contained, offline verification and building of the two-task public release."""
from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path

from .config import safe_name
from .core import digest
from .reextract import independent_terrain
from .results import read, require, scores_agree, verify_results, verify_seal
from .terrain import PARSER_VERSION, LEGACY_PARSER_VERSION, parse_prediction
from .terrain_global import metrics, validate_dataset
from .unified import build as build_unified, combine

DEFAULT_RESULTS = Path("results/2026-10-11")
SCHEMA = "land-water-release/2.0"


def terrain_dataset():
    resource = files("land_water_bench").joinpath("data/terrain-global-dataset.json")
    raw = resource.read_bytes()
    return validate_dataset(json.loads(raw)), digest(raw)


def verify_terrain(directory: Path):
    verify_seal(directory)
    data = read(directory / "data.json")
    reference = read(directory / "reference.details.json")
    metadata = read(directory / "response-metadata.json")
    dataset, checksum = terrain_dataset()
    require(data["state"] == "complete" and data["dataset_sha256"] == reference["dataset_sha256"] == checksum,
            "Terrain result does not use the bundled frozen reference")
    require(reference["prompt_template"] == dataset["prompt_template"], "Terrain prompt changed")
    require(len(reference["points"]) == len(dataset["points"]), "Terrain reference grid is incomplete")
    for point, frozen in zip(reference["points"], dataset["points"]):
        require(all(point[k] == frozen[k] for k in point), "Public terrain reference differs")
    version = data["answer_parser"]
    require(version in {PARSER_VERSION, LEGACY_PARSER_VERSION}, "Unsupported terrain parser")
    checked, names = [], set()
    for model in data["models"]:
        name = model["name"]
        require(safe_name(name) and name not in names and model["details_url"] == name + ".details.json", "Duplicate or unsafe terrain model")
        names.add(name)
        path = directory / model["details_url"]
        require(model["verified"] and digest(path.read_bytes()) == model["details_sha256"], "Terrain response hash differs")
        rows = read(path)
        require(set(rows) == {str(p["id"]) for p in dataset["points"]}, "Incomplete terrain responses")
        evidence = metadata["models"][name]
        for sid, row in rows.items():
            require(row["sample_id"] == int(sid) and row["status"] in {"ok", "invalid", "infra_error"}, "Invalid response identity")
            require(row["label"] in (0, 1) if row["status"] == "ok" else row["label"] is None, "Invalid terrain label")
            if sid in evidence:
                require(digest((row.get("text") or "").encode()) == evidence[sid]["text_sha256"], "Completion evidence differs")
                if evidence[sid]["filtered"] and version == PARSER_VERSION:
                    require(row["status"] == "infra_error", "Content filtering must be unresolved")
            if row["status"] == "infra_error":
                require(row.get("prediction") is None, "Unresolved response has a prediction")
                continue
            if row["complete_response"]:
                prediction, _ = parse_prediction(row["text"], version=version)
            else:
                require(sid in evidence, "Missing incomplete-response evidence")
                prediction = None
            require(row["prediction"] == prediction and (row["status"] == "ok") == (prediction is not None),
                    f"Terrain answer extraction differs: {name}, sample {sid}")
            if prediction:
                require(row["label"] == int(prediction["type"] == "Solid"), "Terrain type/label differ")
        records = {int(k): v for k, v in rows.items()}
        score = metrics(dataset["points"], records)
        require(scores_agree(score, model["metrics"]), "Terrain metrics differ")
        require(score["complete"] and score["pending"] == 0, "Terrain run is pending")
        independent_terrain(dataset["points"], records, score)
        checked.append({"name": name, "records": len(rows), "joint_accuracy_500": score["joint_accuracy"]["500"],
                        "invalid": score["invalid"], "unresolved": score["infra_errors"]})
    require(names == metadata["models"].keys(), "Terrain evidence model set differs")
    require(data["models"] == sorted(data["models"], key=lambda m: (-m["metrics"]["joint_accuracy"]["500"], m["name"])),
            "Terrain ranking is not sorted")
    return {"passed": True, "models": checked, "records": sum(m["records"] for m in checked),
            "dataset_sha256": checksum, "new_api_requests": 0, "independent_scores_verified": True}


def verify_release(directory: Path):
    verify_seal(directory)
    manifest = read(directory / "release.json")
    require(manifest["schema"] == SCHEMA and manifest["tasks"] == {"terrain": "terrain", "classification": "classification"},
            "Unsupported release layout")
    terrain = verify_terrain(directory / "terrain")
    classification = verify_results(directory / "classification")
    combined = combine(read(directory / "terrain/data.json"), read(directory / "classification/data.json"),
                       read(directory / "terrain/reference.details.json"))
    require(manifest["models"] == len(combined["models"])
            and manifest["records"] == terrain["records"] + classification["records"]
            and manifest["points_per_model_per_task"] == combined["points_per_model"], "Release inventory differs")
    return {"passed": True, "release": manifest["release"], "models": len(combined["models"]),
            "records": terrain["records"] + classification["records"], "new_api_requests": 0,
            "terrain": terrain, "classification": classification}


def build_release(directory: Path, output: Path, assets: Path):
    verify_release(directory)
    return build_unified(directory / "terrain", directory / "classification", output, assets)

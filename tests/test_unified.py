import copy

import pytest

from land_water_bench.atlas import build_atlas, grid, tolerance_curve
from land_water_bench.core import make_grid
from land_water_bench.unified import combine


def inputs():
    def model(name, joint, classification):
        return {"name": name, "verified": True, "details_url": name + ".details.json",
                "metrics": {"expected": 16200, "complete": True, "pending": 0,
                            "joint_accuracy": {"500": joint}, "classification_accuracy": .97,
                            "weighted_accuracy": classification}}
    reference = {"dataset_sha256": "terrain-hash",
                 "points": [{k: p[k] for k in ("id", "lat", "lon", "weight")} | {"truth": 0}
                            for p in make_grid()]}
    terrain = {"run_id": "terrain", "updated_at": "now", "state": "complete",
               "dataset_sha256": "terrain-hash", "height_scorable": 15928, "height_unscorable": 272,
               "models": [model("gpt-6-astra", .8, .97), model("seed-2.1-0915", .7, .95),
                          model("gpt-6-1-sol", 1, 1)]}
    classification = {"run_id": "classification", "updated_at": "before", "dataset_sha256": "classification-hash",
                      "truth": "0" * 16200, "models": [model("seed-2.1-0902", .99, .96),
                                                       model("gpt-6-astra", .1, .91)]}
    return terrain, classification, reference


def test_confirmed_seed_aliases_share_a_row_but_preserve_task_sources_and_scores():
    args = inputs()
    originals = copy.deepcopy(args)
    result = combine(*args)
    assert [m["name"] for m in result["models"]] == ["gpt-6-astra", "seed-2.1"]
    astra, seed = result["models"]
    assert astra["terrain"]["metrics"]["classification_accuracy"] == .97
    assert astra["classification"]["metrics"]["weighted_accuracy"] == .91
    assert astra["terrain"]["metrics"]["joint_accuracy"]["500"] == .8
    assert seed["terrain"]["source_model"] == "seed-2.1-0915"
    assert seed["classification"]["source_model"] == "seed-2.1-0902"
    assert seed["terrain"]["details_url"] == "terrain-seed-2.1-0915.details.json"
    assert seed["classification"]["details_url"] == "classification-seed-2.1-0902.details.json"
    assert seed["terrain"]["metrics"]["joint_accuracy"]["500"] == .7
    assert seed["classification"]["metrics"]["weighted_accuracy"] == .96
    assert result["model_aliases"][0]["basis"] == "user_confirmed_same_model"
    assert result["sources"]["terrain"]["model_count"] == result["sources"]["classification"]["model_count"] == 2
    astra["terrain"]["metrics"]["joint_accuracy"]["500"] = .123
    assert args == originals


def test_two_aliases_within_the_same_task_cannot_silently_replace_each_other():
    terrain, classification, reference = inputs()
    another = copy.deepcopy(classification["models"][0])
    another.update(name="seed-2.1-0915", details_url="seed-2.1-0915.details.json")
    classification["models"].append(another)
    with pytest.raises(ValueError, match="Multiple source aliases"):
        combine(terrain, classification, reference)


def test_unconfirmed_seed_aliases_are_not_automatically_merged():
    terrain, classification, reference = inputs()
    classification["models"][0].update(name="seed-2.1-other", details_url="seed-2.1-other.details.json")
    models = {m["name"]: m for m in combine(terrain, classification, reference)["models"]}
    assert models["seed-2.1"]["classification"] is None
    assert models["seed-2.1-other"]["terrain"] is None


@pytest.mark.parametrize("field,value", [("lat", 88), ("weight", .5), ("truth", 1)])
def test_misaligned_coordinates_weights_or_labels_cannot_be_combined(field, value):
    terrain, classification, reference = inputs()
    reference["points"][0][field] = value
    with pytest.raises(ValueError, match="differs|differ"):
        combine(terrain, classification, reference)


def test_pending_or_uncertified_terrain_cannot_be_published():
    terrain, classification, reference = inputs()
    terrain["models"][0]["metrics"]["pending"] = 1
    with pytest.raises(ValueError, match="completed full-grid"):
        combine(terrain, classification, reference)
    terrain["models"][0]["metrics"]["pending"] = 0
    terrain["models"][0]["verified"] = False
    with pytest.raises(ValueError, match="not been verified"):
        combine(terrain, classification, reference)


def test_tolerance_curve_follows_official_inclusion_rules():
    points = [{"id": i, "truth": 1, "weight": 1.0, "elevation_m": 0.0, "height_reference_status": "ok"}
              for i in range(4)]
    points.append({"id": 4, "truth": 1, "weight": 1.0, "elevation_m": None, "height_reference_status": "unsupported"})
    ok = lambda label, height: {"status": "ok", "label": label, "prediction": {"elevation_m": height}}
    records = {0: ok(1, 150), 1: ok(0, 0), 2: {"status": "invalid"}, 3: {"status": "infra_error"}, 4: ok(1, 0)}
    # Unresolved and unscorable cells leave the denominator; invalid and misclassified cells stay wrong.
    assert tolerance_curve(points, records, (100, 200)) == [0, 1 / 3]
    assert grid(points, records)["state"] == "SWIUS"
    metrics = {"joint_accuracy": {"100": 0, "500": 1 / 3, "1000": 1 / 3}}
    with pytest.raises(ValueError, match="disagrees"):
        build_atlas(points, [("m", {"joint_accuracy": {"100": .5, "500": 1 / 3, "1000": 1 / 3}}, records)])
    assert build_atlas(points, [("m", metrics, records)])["models"]["m"]["tolerance_curve"][4] == 1 / 3

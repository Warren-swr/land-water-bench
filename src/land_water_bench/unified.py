"""Combine two frozen tasks in one terrain-first site, without changing saved scores."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from .config import safe_name
from .core import PROMPT, atomic_json, digest, make_grid
from .results import read, require, seal, verify_results, verify_seal

HIDDEN = {"gpt-6-1-sol"}
MODEL_ALIASES = {
    "seed-2.1-0915": "seed-2.1",
    "seed-2.1-0902": "seed-2.1",
}


def combine(terrain, classification, reference):
    require(terrain["state"] == "complete", "Terrain collection is still running")
    require(terrain["dataset_sha256"] == reference["dataset_sha256"], "Terrain reference changed")
    grid = make_grid()
    points = reference["points"]
    require(len(points) == len(grid) == len(classification["truth"]), "Task grids differ")
    for point, expected in zip(points, grid):
        require(all(point[k] == expected[k] for k in ("id", "lat", "lon", "weight")),
                "Coordinate or area weight differs")
        require(str(point["truth"]) == classification["truth"][point["id"]], "Classification references differ")
    models = {}
    for task, collection in [("terrain", terrain), ("classification", classification)]:
        seen = set()
        for source in collection["models"]:
            name = source["name"]
            require(safe_name(name) and name not in seen, "Duplicate or unsafe model alias")
            seen.add(name)
            if name in HIDDEN:
                continue
            metrics = source["metrics"]
            require(metrics["expected"] == len(grid) and metrics["complete"] and not metrics["pending"],
                    "Expected a completed full-grid model")
            if task == "terrain":
                require(source["verified"] and metrics["joint_accuracy"]["500"] is not None,
                        "Terrain scores have not been verified")
            else:
                require(metrics["weighted_accuracy"] is not None, "Missing classification score")
            require(source["details_url"] == name + ".details.json", "Unsafe result path")
            identity = MODEL_ALIASES.get(name, name)
            model = models.setdefault(identity, {"name": identity, "terrain": None, "classification": None})
            require(model[task] is None, "Multiple source aliases for the same model and task")
            model[task] = {"source_model": name, "metrics": copy.deepcopy(metrics),
                           "settings": copy.deepcopy(source.get("settings", {})),
                           "details_url": task + "-" + source["details_url"]}
    # Only explicitly confirmed aliases are merged; retain each task's original identity.
    ordered = sorted(models.values(), key=lambda m: (
        0 if m["terrain"] else 1,
        -(m["terrain"]["metrics"]["joint_accuracy"]["500"] if m["terrain"]
          else m["classification"]["metrics"]["weighted_accuracy"]), m["name"]))
    return {"schema": "land-water-unified/1.0", "state": "complete", "weighting": "cos(latitude)",
            "points_per_model": len(grid), "height_scorable": terrain["height_scorable"],
            "height_unscorable": terrain["height_unscorable"], "models": ordered,
            "model_aliases": [{"name": identity, "aliases": sorted(
                alias for alias, canonical in MODEL_ALIASES.items() if canonical == identity),
                "basis": "user_confirmed_same_model"}
                for identity in sorted(set(MODEL_ALIASES.values())) if identity in models],
            "sources": {"terrain": {"run_id": terrain["run_id"], "dataset_sha256": terrain["dataset_sha256"],
                        "answer_parser": terrain.get("answer_parser"), "reextraction": copy.deepcopy(terrain.get("reextraction")),
                        "updated_at": terrain["updated_at"], "model_count": sum(bool(m["terrain"]) for m in ordered)},
                        "classification": {"run_id": classification["run_id"],
                        "answer_parser": classification.get("answer_parser"), "reextraction": copy.deepcopy(classification.get("reextraction")),
                        "source_collection": classification.get("source_collection"),
                        "dataset_sha256": classification["dataset_sha256"], "updated_at": classification["updated_at"],
                        "model_count": sum(bool(m["classification"]) for m in ordered)}}}


def result_table(data):
    pct = lambda value: "—" if value is None else f"{100 * value:.2f}%"
    lines = ["# Land / Water / Elevation Bench", "",
             "同一个全球网格，两次独立任务。主榜按分类与高程同时正确（±500 m）的面积加权准确率降序排列。",
             "仅分类列来自旧的独立分类任务，不是从联合任务响应重新抽出的分类成绩。", "",
             "| 模型 | 联合 ±500 m | 高程 MAE (m) | 仅分类任务准确率 | 联合任务覆盖率 |",
             "|---|---:|---:|---:|---:|"]
    for model in data["models"]:
        terrain = model["terrain"]["metrics"] if model["terrain"] else {}
        classification = model["classification"]["metrics"] if model["classification"] else {}
        mae = terrain.get("elevation_mae_m")
        lines.append(f"| {model['name']} | {pct(terrain.get('joint_accuracy', {}).get('500'))} | "
                     f"{'—' if mae is None else f'{mae:.1f}'} | {pct(classification.get('weighted_accuracy'))} | "
                     f"{pct(terrain.get('coverage'))} |")
    lines += ["", "## 指标与来源", "",
              "- 两个任务使用相同的 16,200 个坐标、面积权重和 GSHHG 分类标签；旧任务的 Land 对应联合任务的 Solid。",
              "- 高程主指标要求类型正确且高程误差 ≤500 m，仅在 15,928 个有效高程参考点上计分。",
              "- 联合任务内部的分类准确率另列于模型详情，不与独立分类任务列混用。",
              "- 无效计错；未决按各任务原始规则排除并保留覆盖率。高程 MAE 只计算有效数值。",
              "- 用户已确认 Seed 2.1 的 0915 与 0902 是同一模型，统一展示为 Seed 2.1；联合任务保留 0915 原始记录，仅分类任务保留 0902 原始记录。",
              ("- 两项任务已使用新版抽取规则重算：兼容解释正文、公式、重复答案和明确更正；内容过滤统一归为未决。原始正文、参考和已有有效预测保持不变。"
               if all(s.get("reextraction") for s in data["sources"].values()) else
               "- 此展示沿用已冻结分数。已诊断的公式大括号抽取、重复答案与内容过滤归类问题仍存在于该联合任务快照。"),
              "- 模型名称为网关实验别名；各模型推理预算及两次任务并非严格匹配的控制实验。", ""]
    for task, source in data["sources"].items():
        lines += [f"- {task}: {source['run_id']}", f"  - dataset SHA-256: {source['dataset_sha256']}",
                  f"  - source data SHA-256: {source['data_sha256']}"]
    return "\n".join(lines) + "\n"


def build(terrain_dir: Path, classification_dir: Path, output: Path, assets: Path):
    require(not output.exists(), "Use a fresh output directory; frozen sites are never overwritten")
    terrain_seal = verify_seal(terrain_dir)
    classification_proof = verify_results(classification_dir)
    terrain = read(terrain_dir / "data.json")
    classification = read(classification_dir / "data.json")
    reference = read(terrain_dir / "reference.details.json")
    data = combine(terrain, classification, reference)
    data.update(run_id=output.name, built_at=datetime.now(timezone.utc).isoformat())
    for task, directory in [("terrain", terrain_dir), ("classification", classification_dir)]:
        data["sources"][task]["data_sha256"] = digest((directory / "data.json").read_bytes())
    output.mkdir(parents=True)
    for model in data["models"]:
        for task, directory in [("terrain", terrain_dir), ("classification", classification_dir)]:
            entry = model[task]
            if entry is None:
                continue
            source = directory / (entry["source_model"] + ".details.json")
            checksum = digest(source.read_bytes())
            if task == "terrain":
                original = next(m for m in terrain["models"] if m["name"] == entry["source_model"])
                require(original["details_sha256"] == checksum, "Verified terrain responses changed")
            entry["details_sha256"] = checksum
            shutil.copy2(source, output / entry["details_url"])
    public_reference = copy.deepcopy(reference)
    public_reference["classification_prompt_template"] = PROMPT
    atomic_json(output / "reference.details.json", public_reference)
    atomic_json(output / "data.json", data)
    (output / "RESULTS.md").write_text(result_table(data))
    project = assets.resolve().parent.parent
    for name in ("index.html", "style.css", "app.js"):
        shutil.copy2(assets / name, output / name)
    for name in ("display.ttf", "FONT-LICENSE.txt"):
        shutil.copy2(assets.parent / name, output / name)
    for source, target in [("LICENSE", "LICENSE"), ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
                           ("licenses/LGPL-3.0.txt", "GSHHG-LICENSE.txt"), ("licenses/GPL-3.0.txt", "GSHHG-GPL.txt")]:
        shutil.copy2(project / source, output / target)
    (output / ".nojekyll").touch()
    proof = {"passed": True, "models": len(data["models"]), "reference_coordinates_labels_weights_match": True,
             "model_join": "exact_alias_with_confirmed_aliases", "model_aliases": data["model_aliases"],
             "scores_unchanged": True, "new_api_requests": 0,
             "terrain_source_files_verified": len(terrain_seal), "classification": classification_proof,
             "sources": data["sources"]}
    atomic_json(output / "verification.json", proof)
    seal(output)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terrain", type=Path, required=True)
    parser.add_argument("--classification", type=Path, default=Path("results/2026-10-10"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=Path("web/unified"))
    args = parser.parse_args()
    print(json.dumps(build(args.terrain, args.classification, args.output, args.assets), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

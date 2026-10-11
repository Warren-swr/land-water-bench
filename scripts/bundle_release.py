"""Copy a verified two-task result release into a portable public repository directory."""
import argparse
import copy
import json
from pathlib import Path
import shutil

from land_water_bench.core import atomic_json, digest
from land_water_bench.release import SCHEMA, verify_release
from land_water_bench.results import read, require, result_table, seal, verify_results, verify_seal
from land_water_bench.unified import combine, result_table as unified_table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Directory containing terrain/ and classification/")
    parser.add_argument("--audit", type=Path, required=True, help="Sealed re-extraction audit directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Frozen releases cannot be overwritten")
    verify_seal(args.audit)
    args.output.mkdir(parents=True)
    source_hashes = {}
    for task in ("terrain", "classification"):
        source, target = args.source / task, args.output / task
        verify_seal(source)
        if task == "classification":
            verify_results(source)
        target.mkdir()
        data = read(source / "data.json")
        source_hashes[task] = digest((source / "data.json").read_bytes())
        names = ["data.json", "reference.details.json", "reextraction.json"]
        names += [m["details_url"] for m in data["models"]]
        for name in names:
            shutil.copy2(source / name, target / name)
        metadata = {"models": {}, "note": "Provider completion metadata; raw source SHA-256 retained without private paths."}
        for model in data["models"]:
            audit = read(args.audit / f"{task}-{model['name']}.json")
            metadata["models"][model["name"]] = copy.deepcopy(audit["incomplete_metadata"])
        atomic_json(target / "response-metadata.json", metadata)
        if task == "classification":
            provenance = read(source / "provenance.json")
            for model in provenance["models"].values():
                recovery = model.get("unresolved_recovery")
                if recovery and Path(recovery.get("run_id", "")).is_absolute():
                    recovery["run_id"] = "/".join(Path(recovery["run_id"]).parts[-3:])
            atomic_json(target / "provenance.json", provenance)
            (target / "RESULTS.md").write_text(result_table(data))
        else:
            lines = ["# Global terrain results", "", "Area-weighted joint accuracy requires the correct type and height within ±500 m.", "",
                     "| Model | Joint ±500 m | Invalid | Unresolved |", "|---|---:|---:|---:|"]
            for model in data["models"]:
                m = model["metrics"]
                lines.append(f"| {model['name']} | {m['joint_accuracy']['500']:.4%} | {m['invalid']} | {m['infra_errors']} |")
            lines += ["", "Reference coverage: 15,928 height-scorable points / 16,200 requested coordinates.",
                      "Invalid answers count as wrong; unresolved requests are excluded with coverage retained.",
                      "Saved recovery batches increased timeout attempts to eight in total; ordinary rate-limit retry budgets were retained.", ""]
            (target / "RESULTS.md").write_text("\n".join(lines))
        seal(target)
    data = combine(read(args.output / "terrain/data.json"), read(args.output / "classification/data.json"),
                   read(args.output / "terrain/reference.details.json"))
    records = sum(m[task]["metrics"]["expected"] for m in data["models"] for task in ("terrain", "classification") if m[task])
    manifest = {"schema": SCHEMA, "release": args.output.name,
                "tasks": {"terrain": "terrain", "classification": "classification"},
                "source_data_sha256": source_hashes,
                "models": len(data["models"]), "points_per_model_per_task": data["points_per_model"], "records": records,
                "new_api_requests": 0,
                "publication_changes": ["absolute recovery path replaced with run identifier",
                    "only result data, completion metadata and reports are bundled; assets are built from web/"]}
    atomic_json(args.output / "release.json", manifest)
    for task in source_hashes:
        data["sources"][task]["data_sha256"] = source_hashes[task]
    (args.output / "RESULTS.md").write_text(unified_table(data))
    seal(args.output)
    proof = verify_release(args.output)
    print(json.dumps({"passed": proof["passed"], "models": proof["models"], "records": proof["records"]}))


if __name__ == "__main__":
    main()

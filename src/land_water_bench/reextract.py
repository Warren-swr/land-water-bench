"""Versioned offline re-extraction of frozen terrain and classification results."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

from .answers import VERSION as CLASS_PARSER, parse_final_answer
from .core import atomic_json, digest, extract_answer, make_dataset, response_is_filtered
from .results import (pixels, read, require, rescore, result_table, scores_agree,
                      seal, verify_results, verify_seal)
from .terrain import PARSER_VERSION, TOLERANCES, parse_prediction
from .terrain_global import metrics as terrain_metrics
from .terrain_global_collection import write_report
from .unified import build as build_unified


def response_metadata(paths, needed, original, wire):
    """Read only incomplete responses; all other completion gates are already frozen."""
    found, sources = {}, []
    for source in paths:
        path = Path(source)
        checksums = dict((name, checksum) for checksum, name in
                         (line.split("  ", 1) for line in (path.parent / "FILES.sha256").read_text().splitlines()))
        expected = checksums[path.name]
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for line_number, line in enumerate(handle, 1):
                hasher.update(line)
                match = re.search(rb'"sample_id"\s*:\s*(\d+)', line[:200])
                if not match or int(match[1]) not in needed:
                    continue
                row = json.loads(line)
                sid = row["sample_id"]
                text, complete = extract_answer(wire, row["response"])
                saved = original[str(sid)]
                require(text == saved["text"], "Raw and published answer bodies differ")
                for field in ("attempts", "http_status"):
                    if field in saved:
                        require(row.get(field) == saved[field], "Raw response identity differs")
                require(not complete, "Incomplete-response evidence differs")
                response = row["response"]
                found[sid] = {"filtered": response_is_filtered(response),
                    "stop_reason": response.get("stop_reason"),
                    "incomplete_details": response.get("incomplete_details"),
                    "output_tokens": (response.get("usage") or {}).get("output_tokens"),
                    "text_sha256": digest(text.encode()), "source_sha256": expected,
                    "source_line": line_number}
        require(hasher.hexdigest() == expected, "Raw response checksum mismatch")
        sources.append({"path": str(path), "sha256": expected})
    require(set(found) == needed, "Missing raw metadata for incomplete responses")
    return found, sources


def project(row, task, complete, filtered=False):
    result = copy.deepcopy(row)
    if row["status"] == "infra_error":
        return result
    version = PARSER_VERSION if task == "terrain" else CLASS_PARSER
    if filtered:
        result.update(status="infra_error", label=None, complete_response=False,
                      error_type="content_filter", extraction={"version": version, "rule": "content_filter"})
        if task == "terrain":
            result["prediction"] = None
        return result
    if task == "terrain":
        prediction, rule = parse_prediction(row["text"]) if complete else (None, "incomplete_response")
        label = int(prediction["type"] == "Solid") if prediction else None
        result.update(prediction=prediction, extraction={"version": version, "rule": rule})
    else:
        answer = parse_final_answer(row["text"]) if complete else {
            "version": version, "label": None, "rule": None, "reason": "incomplete_response",
            "span": None, "matched_text": None}
        label = answer["label"]
        result["extraction"] = answer
    result.update(status="ok" if label is not None else "invalid", label=label, complete_response=complete)
    return result


def independent_terrain(points, rows, score):
    included = [p for p in points if rows[p["id"]]["status"] != "infra_error"]
    height = [p for p in included if p["height_reference_status"] == "ok"]
    weight = lambda p: math.cos(math.radians(p["lat"]))
    denominator = math.fsum(weight(p) for p in height)
    valid = [p for p in height if rows[p["id"]]["status"] == "ok"]
    for tolerance in TOLERANCES:
        numerator = math.fsum(weight(p) for p in valid if rows[p["id"]]["label"] == p["truth"]
                             and abs(rows[p["id"]]["prediction"]["elevation_m"] - p["elevation_m"]) <= tolerance)
        expected = numerator / denominator if denominator else None
        require(scores_agree(expected, score["joint_accuracy"][str(tolerance)]), "Independent joint score differs")
    if valid:
        mae = math.fsum(weight(p) * abs(rows[p["id"]]["prediction"]["elevation_m"] - p["elevation_m"])
                        for p in valid) / math.fsum(weight(p) for p in valid)
        require(math.isclose(mae, score["elevation_mae_m"], abs_tol=1e-8), "Independent MAE differs")
    numerator = math.fsum(weight(p) for p in included
                         if rows[p["id"]]["status"] == "ok" and rows[p["id"]]["label"] == p["truth"])
    denominator = math.fsum(weight(p) for p in included)
    expected = numerator / denominator if denominator else None
    require(scores_agree(expected, score["classification_accuracy"]), "Independent terrain classification differs")


def revise_collection(source, destination, task, evidence, audit_dir):
    verify_seal(source)
    if task == "classification":
        verify_results(source)
    data = read(source / "data.json")
    provenance = read(source / "provenance.json") if task == "classification" else None
    reference = read(source / "reference.details.json")
    points = reference["points"] if task == "terrain" else make_dataset()["points"]
    require(data.get("state", "complete") == "complete", "Cannot revise a running collection")
    require(not destination.exists(), "Use a new result directory")
    shutil.copytree(source, destination)
    (destination / "FILES.sha256").unlink()
    audits = {}
    for model in data["models"]:
        name = model["name"]
        original = read(source / model["details_url"])
        require(set(original) == {str(p["id"]) for p in points}, "Incomplete response grid")
        if task == "terrain":
            require(model["verified"] and model["details_sha256"] == digest((source / model["details_url"]).read_bytes()),
                    "Unverified terrain source")
            needed = {int(i) for i, r in original.items()
                      if r["status"] != "infra_error" and not r["complete_response"]}
            wire = model["settings"]["wire"]
            independent_terrain(points, {int(i): r for i, r in original.items()}, model["metrics"])
        else:
            needed = set(provenance["models"][name]["incomplete_response_ids"])
            wire = provenance["models"][name]["wire"]
        metadata, sources = response_metadata(evidence.get(name, []), needed, original, wire)
        updated, decisions = {}, []
        for sid, before in original.items():
            complete = before.get("complete_response", int(sid) not in needed)
            filtered = metadata.get(int(sid), {}).get("filtered", False)
            after = project(before, task, complete, filtered)
            require(after.get("text") == before.get("text"), "Answer text changed")
            if before["status"] == "ok":
                require(after["status"] == "ok" and after["label"] == before["label"]
                        and after.get("prediction") == before.get("prediction"), "Previously valid answer changed")
            elif before["status"] == "infra_error":
                require(after == before, "Previously unresolved response changed")
            updated[sid] = after
            if (after["status"], after["label"], after.get("prediction")) != (
                    before["status"], before["label"], before.get("prediction")):
                decisions.append({"sample_id": int(sid), "before_status": before["status"],
                    "after_status": after["status"], "before_label": before["label"],
                    "after_label": after["label"], "prediction": after.get("prediction"),
                    "extraction": after.get("extraction"), "text_sha256": digest((after.get("text") or "").encode())})
        rows = {int(i): r for i, r in updated.items()}
        before_score = copy.deepcopy(model["metrics"])
        if task == "terrain":
            model["metrics"] = terrain_metrics(points, rows)
            independent_terrain(points, rows, model["metrics"])
            model["answer_parser"] = PARSER_VERSION
        else:
            model["metrics"], _ = rescore(points, rows)
            model["pixels"] = pixels(rows)
            info = provenance["models"][name]
            info["incomplete_response_ids"] = sorted(i for i in needed if not metadata[i]["filtered"])
            info["unresolved_ids"] = sorted(i for i, r in rows.items() if r["status"] == "infra_error")
            info["content_filter_ids"] = sorted(i for i in needed if metadata[i]["filtered"])
            info["invalid_reasons"] = {str(i): r["extraction"]["reason"] for i, r in rows.items() if r["status"] == "invalid"}
        atomic_json(destination / model["details_url"], updated)
        if task == "terrain":
            model["details_sha256"] = digest((destination / model["details_url"]).read_bytes())
        audit = {"before": before_score, "after": model["metrics"], "decisions": decisions,
                 "incomplete_metadata": metadata, "raw_sources": sources,
                 "records": len(rows), "previous_valid_answers_unchanged": True, "answer_texts_unchanged": True}
        atomic_json(audit_dir / f"{task}-{name}.json", audit)
        audits[name] = {k: v for k, v in audit.items() if k not in {"incomplete_metadata", "raw_sources"}}
        print(json.dumps({"task": task, "model": name, "changed": len(decisions),
                          "invalid": model["metrics"]["invalid"], "unresolved": model["metrics"]["infra_errors"]}), flush=True)
    data.update(run_id=destination.parent.name + "-" + task, updated_at=datetime.now(timezone.utc).isoformat(),
                answer_parser=PARSER_VERSION if task == "terrain" else CLASS_PARSER,
                reextraction={"source_run_id": read(source / "data.json")["run_id"],
                    "source_data_sha256": digest((source / "data.json").read_bytes()),
                    "new_api_requests": 0, "content_filter_policy": "unresolved",
                    "previous_valid_answers_unchanged": True, "answer_texts_unchanged": True})
    metric = (lambda m: m["metrics"]["joint_accuracy"]["500"]) if task == "terrain" else (lambda m: m["metrics"]["weighted_accuracy"])
    data["models"].sort(key=lambda m: (-metric(m), m["name"]))
    atomic_json(destination / "data.json", data)
    if task == "classification":
        provenance.update(release=data["run_id"], answer_parser=CLASS_PARSER, reextraction=data["reextraction"])
        atomic_json(destination / "provenance.json", provenance)
        proof = verify_results(destination, check_seal=False)
        (destination / "RESULTS.md").write_text(result_table(data))
    else:
        proof = {"passed": True, "models": len(audits), "records": sum(a["records"] for a in audits.values()),
                 "independent_joint_classification_and_mae": True, "new_api_requests": 0,
                 "answer_parser": PARSER_VERSION, "reference_unchanged": True}
        write_report(destination, data)
    atomic_json(destination / "verification.json", proof)
    atomic_json(destination / "reextraction.json", data["reextraction"])
    seal(destination)
    return audits


def build(terrain, classification, evidence, output, audit_dir, assets):
    require(not output.exists() and not audit_dir.exists(), "Use fresh output and audit directories")
    output.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    source_dir = Path(__file__).parent
    for name in ("answers.py", "core.py", "terrain.py", "terrain_global.py", "results.py", "reextract.py", "unified.py"):
        target = audit_dir / "source" / name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source_dir / name, target)
    atomic_json(audit_dir / "inputs.json", {"terrain": str(terrain), "classification": str(classification),
        "response_evidence": evidence, "terrain_parser": PARSER_VERSION, "classification_parser": CLASS_PARSER})
    changes = {}
    for task, source in [("terrain", terrain), ("classification", classification)]:
        changes[task] = revise_collection(source, output / task, task, evidence.get(task, {}), audit_dir)
    proof = build_unified(output / "terrain", output / "classification", output / "site", assets)
    summary = {"passed": True, "new_api_requests": 0, "changes": changes, "site_verification": proof}
    atomic_json(audit_dir / "summary.json", summary)
    seal(audit_dir)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terrain", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--response-evidence", type=Path, required=True,
                        help="JSON mapping task -> model -> sealed JSONL source paths for incomplete responses")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=Path("web/unified"))
    args = parser.parse_args()
    result = build(args.terrain, args.classification, read(args.response_evidence), args.output, args.audit_dir, args.assets)
    print(json.dumps({"passed": result["passed"], "site": str(args.output / "site"), "new_api_requests": 0}))


if __name__ == "__main__":
    main()

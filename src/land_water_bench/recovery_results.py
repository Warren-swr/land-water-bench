"""Verify selective recovery evidence and build new frozen task collections."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from datetime import datetime, timezone

from .answers import reextract_record
from .core import atomic_json, digest, make_dataset, read_jsonl
from .results import (pixels, read, require, rescore, result_table, seal, verify_results, verify_seal)
from .terrain_global_collection import verify_model, write_report
from .unified import build


def copy_collection(source, output):
    require(not output.exists(), "Use a new collection directory")
    verify_seal(source)
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("FILES.sha256"))


def terrain_collection(root, baseline, output, reference_path):
    copy_collection(baseline, output)
    data = read(output / "data.json")
    dataset = read(reference_path)
    require(digest(reference_path.read_bytes()) == data["dataset_sha256"], "Reference changed")
    changes = []
    for name, launch in read(root / "launches.json").items():
        run = Path(launch["run_dir"])
        if launch["task"] != "terrain":
            continue
        manifest = read(run / "manifest.json")
        receipt = manifest["recovery"]
        selected = set(receipt["sample_ids"])
        source = Path(receipt["source_run"])
        require(digest((source / "FILES.sha256").read_bytes()) == receipt["source_seal_sha256"],
                "Parent run seal changed")
        original = read(baseline / (name + ".details.json"))
        require(selected == {int(k) for k, r in original.items() if r["status"] == "infra_error"},
                "Recovery selected a completed answer")
        # The branch keeps all original attempts. Their exact bytes must remain a prefix.
        with (source / "attempts.jsonl").open("rb") as before, (run / "attempts.jsonl").open("rb") as after:
            while block := before.read(1024 * 1024):
                require(after.read(len(block)) == block, "Historical attempt evidence changed")
            prior = read(run / "launch-validation.json")["previous_attempts"]
            requested = set()
            for line in after:
                event = json.loads(line)
                sid = event["sample_id"]
                require(sid in selected and event["model"] == name, "An ordinary answer was retried")
                require(prior[str(sid)] < event["attempt"] <= receipt["cumulative_attempt_limit"],
                        "Cumulative attempt budget changed")
                if event["event"] == "start":
                    requested.add(sid)
            require(requested == selected, "A selected recovery request was not sent")
        proof = verify_model(run, dataset, data["dataset_sha256"], output / (name + ".details.json"))
        final = read(output / (name + ".details.json"))
        require(all(final[sid] == row for sid, row in original.items() if int(sid) not in selected),
                "A non-selected response changed")
        summary = read(run / "summary.json")
        model = next(m for m in data["models"] if m["name"] == name)
        before = model["metrics"]["infra_errors"]
        model.update(metrics=summary["models"][0]["metrics"], verified=True, status="complete",
                     completed=16200, inflight=0, retries=summary["retries"], circuit=summary["models"][0]["circuit"],
                     details_sha256=proof["public_details_sha256"])
        changes.append({"model": name, "selected": len(selected), "previous_unresolved": before,
                        "remaining_unresolved": model["metrics"]["infra_errors"],
                        "valid_responses_recovered": sum(final[str(i)]["status"] == "ok" for i in selected),
                        "new_invalid": sum(final[str(i)]["status"] == "invalid" for i in selected)})
    data["run_id"] = output.name
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["models"].sort(key=lambda m: (-m["metrics"]["joint_accuracy"]["500"], m["name"]))
    data["recovery"] = {"source_collection": baseline.name, "models": changes,
                        "max_timeout_attempts_total": 8, "original_completed_answers_preserved": True}
    atomic_json(output / "data.json", data)
    write_report(output, data)
    report = output / "RESULTS.md"
    text = report.read_text().replace("读取超时最多重试两次", "初轮读取超时最多重试两次；本轮未决恢复将累计重试上限增加到七次")
    text += "\n## 未决请求恢复\n\n本轮仅替换原有未决请求；其余原始回答保持不变。首次恢复请求若已有读取超时，继续使用 600 秒时限；累计限流重试预算不重置。\n\n"
    for change in changes:
        text += f"- {change['model']}：原未决 {change['previous_unresolved']}，本轮恢复有效 {change['valid_responses_recovered']}，新增无效 {change['new_invalid']}，仍未决 {change['remaining_unresolved']}。\n"
    report.write_text(text)
    atomic_json(output / "recovery-verification.json", {"passed": True, "changes": changes,
                 "retained_answers_unchanged": True, "historical_attempts_preserved": True})
    seal(output)
    return changes


def classification_collection(run, baseline, output):
    copy_collection(baseline, output)
    name = "kimi-k3"
    summary = read(run / "summary.json")
    require(summary["state"] == "complete", "Classification recovery is unfinished")
    config = read(run / "config.json")
    recovery = config["unresolved_recovery"]
    require(digest((baseline / (name + ".details.json")).read_bytes()) == recovery["source_details_sha256"],
            "Classification baseline changed")
    original = read(output / (name + ".details.json"))
    selected = set(recovery["sample_ids"])
    require(selected == {int(k) for k, r in original.items() if r["status"] == "infra_error"},
            "Classification selection is not unresolved-only")
    rows = read_jsonl(run / (name + ".jsonl"))
    require(len(rows) == len(selected) and {r["sample_id"] for r in rows} == selected,
            "Unexpected or missing recovery requests")
    dataset = make_dataset()
    updated = dict(original)
    for row in rows:
        sid = row["sample_id"]
        request = row["request"]
        require(request["messages"] == [{"role": "user", "content": dataset["points"][sid]["prompt"]}]
                and request["model"] == config["models"][0]["requested_model"], "Request changed")
        require(not any(k in request for k in ("tools", "system", "instructions", "previous_response_id")),
                "Unexpected agent context")
        if row["status"] != "infra_error":
            parsed = reextract_record(row, config["models"][0]["wire"])
            require(all(row[k] == parsed[k] for k in ("status", "label", "text", "extraction")),
                    "Classification extraction changed")
        updated[str(sid)] = {k: row.get(k) for k in ("sample_id", "status", "label", "text")}
    require(all(updated[sid] == row for sid, row in original.items() if int(sid) not in selected),
            "Non-selected classification response changed")
    score, _ = rescore(dataset["points"], {int(k): v for k, v in updated.items()})
    data = read(output / "data.json")
    model = next(m for m in data["models"] if m["name"] == name)
    model.update(metrics=score, pixels=pixels({int(k): v for k, v in updated.items()}))
    data["models"].sort(key=lambda m: (-m["metrics"]["weighted_accuracy"], m["name"]))
    data.update(run_id=output.name, source_collection=baseline.name,
                updated_at=datetime.now(timezone.utc).isoformat())
    provenance = read(output / "provenance.json")
    provenance["release"] = output.name
    model_provenance = provenance["models"][name]
    model_provenance["unresolved_ids"] = sorted(int(k) for k, r in updated.items() if r["status"] == "infra_error")
    model_provenance["unresolved_recovery"] = {"source_collection": baseline.name,
        "sample_ids": sorted(selected), "run_id": str(run), "max_output_tokens": 65536,
        "concurrency_limit": config["concurrency"], "observed_peak": summary["peak_inflight"],
        "previous_attempts_preserved": True}
    incomplete = set(model_provenance["incomplete_response_ids"])
    for row in rows:
        if row["status"] != "infra_error" and not row.get("complete_response"):
            incomplete.add(row["sample_id"])
        if row["status"] == "invalid":
            model_provenance["invalid_reasons"][str(row["sample_id"])] = row["extraction"].get("reason") or row["extraction"].get("rule")
    model_provenance["incomplete_response_ids"] = sorted(incomplete)
    atomic_json(output / (name + ".details.json"), updated)
    atomic_json(output / "data.json", data)
    atomic_json(output / "provenance.json", provenance)
    (output / "RESULTS.md").write_text(result_table(data))
    proof = verify_results(output, check_seal=False)
    atomic_json(output / "verification.json", proof)
    seal(output)
    atomic_json(run / "recovery-verification.json", {"passed": True, "selected": sorted(selected),
                "retained_answers_unchanged": True, "scored_points": 16200})
    seal(run)
    return {"model": name, "selected": len(selected), "remaining_unresolved": score["infra_errors"],
            "valid_responses_recovered": sum(r["status"] == "ok" for r in rows),
            "new_invalid": sum(r["status"] == "invalid" for r in rows)}

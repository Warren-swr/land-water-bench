"""Audit individual global runs and publish a live, credential-free collection."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import math
from pathlib import Path
import random
import shutil
import tempfile
import time

from .core import atomic_json, digest, read_jsonl
from .results import require, scores_agree, seal
from .terrain import TOLERANCES, project_answer
from .terrain_global import metrics, validate_dataset

SAFE_FIELDS = ("sample_id", "status", "label", "prediction", "text", "complete_response", "extraction", "attempts", "latency_seconds", "http_status")
HIDDEN = {"gpt-6-1-sol"}


def verify_model(run, dataset, expected_hash, destination):
    """The runner lock must be free: never certify a process still writing evidence."""
    with (run / ".runner.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads((run / "manifest.json").read_text())
        summary = json.loads((run / "summary.json").read_text())
        require(summary["state"] == "complete", "Model run has not completed")
        require(not summary["config"]["limit"] and len(summary["models"]) == 1, "Expected one model on the full grid")
        require(manifest["dataset_sha256"] == expected_hash == digest((run / "dataset.json").read_bytes()), "Frozen dataset mismatch")
        for name, checksum in manifest["execution_source_sha256"].items():
            require(digest((run / "source" / name).read_bytes()) == checksum, "Execution source hash mismatch")
        model = summary["models"][0]
        rows = read_jsonl(run / (model["name"] + ".jsonl"))
        records = {row["sample_id"]: row for row in rows}
        require(len(rows) == len(records) == 16200 and set(records) == set(range(16200)), "Missing or duplicate coordinates")
        for point in dataset["points"]:
            row = records[point["id"]]
            request = row["request"]
            require(row["model"] == model["name"] and request["model"] == model["requested_model"], "Model identity mismatch")
            require(not any(k in request for k in ("tools", "system", "instructions", "previous_response_id")), "Unexpected agent context")
            messages = request.get("input", request.get("messages"))
            require(messages == [{"role":"user", "content":point["prompt"]}], "Actual prompt differs")
            if row["status"] != "infra_error":
                parsed = project_answer(row, model["wire"], version=summary["config"]["answer_parser"])
                require(all(row[k] == value for k, value in parsed.items()), "Answer extraction differs")
        indices = list(range(len(dataset["points"])))
        random.Random(summary["config"]["shuffle_seed"]).shuffle(indices)
        # Reproduce the frozen accumulation order; the independent checks below use fsum.
        result = metrics([dataset["points"][i] for i in indices], records)
        require(scores_agree(result, model["metrics"]), "Saved metrics differ")
        included = [p for p in dataset["points"] if records[p["id"]]["status"] != "infra_error"]
        height_included = [p for p in included if p["height_reference_status"] == "ok"]
        weight = lambda p: math.cos(math.radians(p["lat"]))
        denominator = math.fsum(weight(p) for p in height_included)
        for tolerance in TOLERANCES:
            numerator = math.fsum(weight(p) for p in height_included
                if records[p["id"]]["status"] == "ok" and records[p["id"]]["label"] == p["truth"]
                and abs(records[p["id"]]["prediction"]["elevation_m"] - p["elevation_m"]) <= tolerance)
            independent = numerator / denominator if denominator else None
            actual = result["joint_accuracy"][str(tolerance)]
            require(actual is None if independent is None else actual is not None and math.isclose(actual, independent, abs_tol=1e-12), "Independent weighted joint score differs")
        good = [p for p in height_included if records[p["id"]]["status"] == "ok"]
        if good:
            mae = math.fsum(weight(p)*abs(records[p["id"]]["prediction"]["elevation_m"]-p["elevation_m"]) for p in good)/math.fsum(weight(p) for p in good)
            require(math.isclose(result["elevation_mae_m"],mae,abs_tol=1e-8), "Independent MAE differs")
        public = {str(i): {k:row[k] for k in SAFE_FIELDS if k in row} for i,row in records.items()}
        atomic_json(destination, public)
        audit = {"passed":True, "model":model["name"], "records":16200, "dataset_sha256":expected_hash,
                 "prompt_and_no_tools_verified":True, "independent_weighted_joint_and_mae":True,
                 "public_details_sha256":digest(destination.read_bytes()), "metrics":result}
        if not (run / "FILES.sha256").exists():
            atomic_json(run / "global-verification.json", audit)
            seal(run)
        else:
            previous = json.loads((run / "global-verification.json").read_text())
            require(previous == audit, "Previously sealed verification differs")
        return audit


def write_report(output, metadata):
    pct = lambda x: "—" if x is None else f"{100*x:.2f}%"
    num = lambda x: "—" if x is None else f"{x:,.1f}"
    lines = ["# Global Terrain / Elevation", "", f"Collection: `{metadata['run_id']}`", "",
        "全球原始 2° 网格的 16,200 个坐标全部请求。输入经纬度，输出 Solid/Water 和地表或水底高程。",
        "", "## 当前结果", "", "仅通过完整性与独立算分校验的模型显示最终成绩；运行中不填最终准确率。",
        "", "| 模型 | 状态 | 已返回 | 联合 ±500 m | ±100 m | ±1,000 m | 分类准确率 | MAE (m) | 无效 | 未决 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for model in metadata["models"]:
        s = model.get("metrics") or {}
        joint = s.get("joint_accuracy", {}) if model.get("verified") else {}
        lines.append(f"| {model['name']} | {model['status']} | {model.get('completed',0)} / 16200 | {pct(joint.get('500'))} | {pct(joint.get('100'))} | {pct(joint.get('1000'))} | {pct(s.get('classification_accuracy') if model.get('verified') else None)} | {num(s.get('elevation_mae_m'))} | {s.get('invalid',0)} | {s.get('infra_errors',0)} |")
    lines += ["", "## 固定参考与评分", "",
        f"- 数据集 SHA-256：`{metadata['dataset_sha256']}`。",
        f"- 高程可评分点 {metadata['height_scorable']:,}；高程不可评分点 {metadata['height_unscorable']:,}。不可评分点仍发送给模型，并保留分类结果。",
        "- 分类参考：GSHHG 2.3.7。高程参考：[NOAA ETOPO 2022](https://www.ncei.noaa.gov/products/etopo-global-relief-model)，15 角秒 ice-surface 栅格，EGM2008 基准，四邻近像元双线性插值。",
        "- 普通陆地用裸地高程，接地冰用冰面高程，海洋用海床高程。湖泊、冰架、嵌套岛屿、已知分类冲突和海洋高程异常在高程指标中使用统一的预先冻结掩码；分类仍采用冻结的 GSHHG 定义。",
        "- 主榜为 cos(latitude) 面积加权的联合准确率，主容差 500 m；必须同时类型正确且高程误差达标。另存 100/1,000 m、未加权成绩、各类型及高程档细分。",
        "- 无效完整回答计错；未决 API 请求从分子和分母剔除，数量和覆盖率保留。高程 MAE 仅计算有效数值、参考有效的回答，包含类型判断错误。",
        "- 参考有测量误差和网格分辨率限制。高程容差表示相对冻结栅格的误差，不表示物理世界中的绝对精度。与 512 题均衡试验的抽样与权重不同，分数不直接比较。", "",
        "## 调用与证据", "",
        "- 每点一次独立直接 API 请求，无 Agent、工具、地图、系统提示词或历史对话。输出上限 65,536 tokens。每模型最多 512 并发。",
        "- 限流等可重试错误最多重试 1,000 次。首次超时 300 秒，读取超时后为 600 秒；读取超时最多重试两次。已返回的正确或错误答案不因得分重跑。",
        "- 模型连接来自本轮冻结的当前工作区配置；Seed 使用 0915。DeepSeek 使用已验证的 `ali-deepseek-v4.1-flash` 路由名，修正当前别名的 404。",
        "- GPT-5.6 Sol/Terra 复用 Astra 的 Responses 接入，仅更换模型名。Responses 请求 low reasoning；Messages 请求 thinking budget 1,024，该兼容字段未必严格限制实际思考长度。不同模型不是匹配推理预算的比较。",
        "- 模型名是网关实验别名，并未独立认证提供方身份。预检和全量运行分开保存；每个正式模型的 16,200 条实际请求、返回、抽取、分数和代码快照均核验后再发布最终分数。",
        "- GPT-6.1 Sol 按此前要求在页面隐藏，其运行记录仍保留。", "", "## 不可用配置", ""]
    for model in metadata.get("unavailable", []):
        lines.append(f"- {model['name']}：{model['reason']}。没有作为零分模型参加排名。")
    (output / "RESULTS.md").write_text("\n".join(lines)+"\n")


class Collection:
    def __init__(self, root, reference, output, assets):
        self.root, self.output = root, output
        self.dataset = validate_dataset(json.loads(reference.read_text()))
        self.hash = digest(reference.read_bytes())
        self.cache = {}
        require(not (output / "FILES.sha256").exists(), "Collection is sealed; use a new output directory")
        output.mkdir(parents=True, exist_ok=True)
        for name in ("index.html", "style.css", "app.js"):
            shutil.copy2(assets / name, output / name)
        for name in ("display.ttf", "FONT-LICENSE.txt"):
            shutil.copy2(assets.parent / name, output / name)
        project = assets.resolve().parent.parent
        for src, dst in [("THIRD_PARTY_NOTICES.md","THIRD_PARTY_NOTICES.md"),("licenses/LGPL-3.0.txt","GSHHG-LICENSE.txt"),("licenses/GPL-3.0.txt","GSHHG-GPL.txt")]:
            shutil.copy2(project / src, output / dst)
        fields = ("id","lat","lon","truth","weight","category","elevation_m","height_reference_status","height_reference_reason","stratum")
        public_reference = {"dataset_sha256":self.hash, "prompt_template":self.dataset["prompt_template"],
                            "points":[{k:p[k] for k in fields} for p in self.dataset["points"]]}
        atomic_json(output / "reference.details.json", public_reference)

    def update(self):
        inventory = json.loads((self.root / "preflight/inventory.json").read_text())
        launches = json.loads((self.root / "launches.json").read_text()) if (self.root / "launches.json").exists() else {}
        entries, unavailable, hidden = [], [], []
        all_done = len(launches) == len(inventory["models"])
        for item in inventory["models"]:
            name = item["name"]
            receipt = launches.get(name)
            entry = {"name":name, "status":"probing", "completed":0, "verified":False,
                     "settings":{k:item[k] for k in ("wire","reasoning","max_output_tokens")}}
            if receipt and not receipt["launched"]:
                evidence = json.loads(Path(receipt["evidence"]).read_text())
                failures = [s["attempts"][-1] for s in evidence["samples"]]
                statuses = sorted({s["status"] for s in failures})
                unavailable.append({"name":name, "reason":"API preflight HTTP " + "/".join(map(str,statuses))})
                continue
            if receipt:
                run = Path(receipt["run_dir"])
                summary_path = run / "summary.json"
                entry["status"] = "starting"
                if summary_path.exists():
                    summary = json.loads(summary_path.read_text())
                    model = summary["models"][0]
                    score = model["metrics"]
                    entry.update(status=summary["state"], metrics=score,
                        completed=score["valid"]+score["invalid"]+score["infra_errors"],
                        inflight=summary["inflight"], retries=summary["retries"], circuit=model["circuit"])
                    if summary["state"] == "complete":
                        directory = self.root / "hidden-projections" if name in HIDDEN else self.output
                        directory.mkdir(parents=True, exist_ok=True)
                        target = directory / (name + ".details.json")
                        if name not in self.cache:
                            try:
                                self.cache[name] = verify_model(run, self.dataset, self.hash, target)
                            except BlockingIOError:
                                entry["status"] = "verifying"
                            except (ValueError, KeyError, OSError) as exc:
                                entry["status"] = "verification_failed"
                                entry["verification_error"] = type(exc).__name__ + ": " + str(exc)
                                atomic_json(self.root / (name + ".verification-error.json"), {"error":str(exc)})
                        if name in self.cache:
                            entry.update(verified=True, status="complete", details_url=target.name,
                                         details_sha256=self.cache[name]["public_details_sha256"])
            if not entry["verified"]:
                all_done = False
            (hidden if name in HIDDEN else entries).append(entry)
        entries.sort(key=lambda m: (0 if m["verified"] else 1, -(m.get("metrics",{}).get("joint_accuracy",{}).get("500") or 0), m["name"]))
        points = self.dataset["points"]
        metadata = {"run_id":self.root.name, "updated_at":datetime.now(timezone.utc).isoformat(),
            "state":"complete" if all_done else "running", "dataset_sha256":self.hash,
            "points_per_model":len(points), "height_scorable":sum(p["height_reference_status"]=="ok" for p in points),
            "height_unscorable":sum(p["height_reference_status"]!="ok" for p in points),
            "weighting":"cos(latitude)", "models":entries, "unavailable":unavailable,
            "hidden_models":[m["name"] for m in hidden], "hidden_completed":sum(m["completed"] for m in hidden),
            "completed":sum(m["completed"] for m in entries), "expected":len(entries)*len(points),
            "all_run_completed":sum(m["completed"] for m in entries+hidden),
            "all_run_expected":(len(entries)+len(hidden))*len(points),
            "verified_models":sum(m["verified"] for m in entries), "candidate_model_count":len(inventory["models"])}
        atomic_json(self.output / "data.json", metadata)
        atomic_json(self.root / "collection-status.json", metadata)
        write_report(self.output, metadata)
        print(json.dumps({"time":metadata["updated_at"],"state":metadata["state"],"completed":metadata["all_run_completed"],"expected":metadata["all_run_expected"],"verified":sum(m["verified"] for m in entries+hidden)}),flush=True)
        return all_done


def export_run(run: Path, output: Path, assets: Path):
    """Export one completed full-grid model without a private collection launcher."""
    summary = json.loads((run / "summary.json").read_text())
    require(summary["state"] == "complete" and len(summary["models"]) == 1 and not summary["config"]["limit"],
            "Export requires one completed full-grid model per run directory")
    require(not output.exists(), "Use a fresh output directory")
    model = summary["models"][0]
    require(model["name"] not in HIDDEN, "This model alias is excluded from the public leaderboard")
    with tempfile.TemporaryDirectory(prefix="lwb-collection-") as temporary:
        root = Path(temporary) / output.name
        atomic_json(root / "preflight/inventory.json", {"models": [
            {k: model[k] for k in ("name", "wire", "reasoning", "max_output_tokens")}]})
        atomic_json(root / "launches.json", {model["name"]: {"launched": True, "run_dir": str(run.resolve())}})
        collection = Collection(root, run / "dataset.json", output, assets)
        require(collection.update(), "Terrain result verification did not complete")
    seal(output)
    return {"passed": True, "models": 1, "records": 16200, "output": str(output), "new_api_requests": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--reference",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--assets",type=Path,default=Path("web/terrain-global"))
    parser.add_argument("--interval",type=float,default=30)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    collection=Collection(args.root,args.reference,args.output,args.assets)
    while True:
        complete=collection.update()
        if complete or args.once or (args.root/"STOP_REQUESTED.json").exists():
            break
        time.sleep(args.interval)
    if complete:
        seal(args.output)
        atomic_json(args.root/"collection-verification.json",{"passed":True,"dataset_sha256":collection.hash,"verified_models":list(collection.cache),"independent_scores":True})


if __name__ == "__main__":
    main()

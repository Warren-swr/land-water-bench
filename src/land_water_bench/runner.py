"""Direct-model runner: one independent request per coordinate, no agent runtime."""

from __future__ import annotations

import argparse
import asyncio
import email.utils
import fcntl
import importlib.metadata
import json
import os
import platform
import random
import shutil
import signal
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import load_models, redact
from .core import (PROTOCOL, SOURCE_URL, atomic_json, digest, extract_answer,
                  make_dataset, metrics, parse_label, read_jsonl, usage_counts)
from .replay import make_plan, source_records
from .answers import VERSION as ANSWER_VERSION, reextract_record
from .scoring import VERSION as SCORE_VERSION, exclude_unresolved

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = ("core.py", "config.py", "runner.py", "replay.py", "answers.py", "scoring.py")
RETRYABLE = {408, 425, 429, 500, 502, 503, 504, 529}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def retry_delay(attempt: int, retry_after: str | None, rng=random) -> float:
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                delay = email.utils.parsedate_to_datetime(retry_after).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                delay = 0
        if delay > 0:
            return delay + rng.uniform(0, 0.5)
    return min(60, 0.5 * 2 ** min(attempt - 1, 7)) * rng.uniform(0.5, 1.5)


def is_content_filter(error) -> bool:
    if not isinstance(error, dict):
        return False
    return error.get("type") == "content_filter" or "DataInspectionFailed" in str(error.get("message", ""))


class Runner:
    protocol = PROTOCOL
    answer_version = ANSWER_VERSION
    score_version = SCORE_VERSION
    source_url = SOURCE_URL
    source_files = SOURCE_FILES

    def make_dataset(self):
        return make_dataset()

    def config_updates(self):
        return {}

    def project_answer(self, row, wire):
        return reextract_record(row, wire)

    def score_records(self, points, records):
        return exclude_unresolved(metrics(points, records))

    def __init__(self, args):
        self.args = args
        self.run = args.run_dir.resolve()
        self.export = args.export_dir.resolve()
        self.models = load_models(args.config, args.models)
        for model in self.models:
            if getattr(args, "max_output_tokens", None) is not None:
                model.max_tokens = args.max_output_tokens
        self.secrets = [value for m in self.models for value in (m.key, m.url)]
        self.records = {m.name: {} for m in self.models}
        self.attempts = Counter()
        self.read_timeouts = set()
        self.timeout_attempts = Counter()
        self.http_counts = Counter()
        self.returned_models = {m.name: set() for m in self.models}
        self.stop = asyncio.Event()
        self.inflight = self.peak_inflight = 0
        self.retries = self.previous_retries = 0
        self.circuits = {}
        self.start = time.monotonic()
        self.state = "running"
        self.handles = {}
        self.lock = None
        self.replay = None

    def prepare(self):
        self.run.mkdir(parents=True, exist_ok=True)
        if (self.run / "FILES.sha256").exists():
            raise ValueError("Run is sealed; use a new run directory")
        self.lock = (self.run / ".runner.lock").open("a+")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.export.mkdir(parents=True, exist_ok=True)
        config = {"protocol": self.protocol, "models": [m.public() for m in self.models],
                  "concurrency": self.args.concurrency, "max_retries": self.args.max_retries,
                  "timeout_seconds": self.args.timeout, "shuffle_seed": self.args.seed,
                  "limit": self.args.limit, "source": self.source_url,
                  "concurrency_scope": "per model", "answer_parser": self.answer_version,
                  "score_policy": self.score_version,
                  "endpoint_sha256": {m.name: digest(m.url.encode()) for m in self.models}}
        config.update(self.config_updates())
        if getattr(self.args, "max_timeout_retries", None) is not None:
            config["max_timeout_retries"] = self.args.max_timeout_retries
        replay_from = getattr(self.args, "replay_truncated_from", None)
        if replay_from:
            if len(self.models) != 1 or self.args.limit:
                raise ValueError("Selective replay requires one model and the full grid")
            self.replay = make_plan(replay_from, self.models[0].public())
            config["replay"] = self.replay
        config_file = self.run / "config.json"
        if config_file.exists():
            if json.loads(config_file.read_text()) != config:
                raise ValueError("Resume configuration differs from the frozen run")
            self.manifest = json.loads((self.run / "manifest.json").read_text())
            if digest((self.run / "dataset.json").read_bytes()) != self.manifest["dataset_sha256"]:
                raise ValueError("Frozen dataset hash mismatch")
            revisions = self.manifest.get("execution_revisions", [])
            expected_hashes = revisions[-1]["sha256"] if revisions else self.manifest["execution_source_sha256"]
            current_hashes = {name: digest((ROOT / name).read_bytes()) for name in expected_hashes}
            if current_hashes != expected_hashes:
                reason = getattr(self.args, "resume_revision", None)
                if not reason:
                    raise ValueError("Runner source changed; use a new run ID or an explicit recorded resume revision")
                revision = self.run / "execution-revisions" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                revision.mkdir(parents=True)
                atomic_json(revision / "manifest-before.json", self.manifest)
                for name in current_hashes:
                    shutil.copy2(ROOT / name, revision / name)
                revisions.append({"created_at": now(), "reason": reason,
                                  "source_directory": str(revision.relative_to(self.run)),
                                  "sha256": current_hashes})
                self.manifest["execution_revisions"] = revisions
                atomic_json(self.run / "manifest.json", self.manifest)
        else:
            dataset = self.make_dataset()
            atomic_json(self.run / "dataset.json", dataset)
            atomic_json(config_file, config)
            snapshot = self.run / "source"
            snapshot.mkdir(exist_ok=True)
            hashes = {}
            for name in self.source_files:
                shutil.copy2(ROOT / name, snapshot / name)
                hashes[name] = digest((ROOT / name).read_bytes())
            self.manifest = {
                "run_id": self.run.name, "created_at": now(),
                "dataset_sha256": digest((self.run / "dataset.json").read_bytes()),
                "execution_source_sha256": hashes, "python": sys.version,
                "platform": platform.platform(), "agent_environment": False, "tools": [],
                "packages": {x: importlib.metadata.version(x) for x in ("httpx", "PyYAML")},
                "gpu": "not used", "container": "not used",
            }
            atomic_json(self.run / "manifest.json", self.manifest)
        self.config = config
        self.dataset = json.loads((self.run / "dataset.json").read_text())
        if self.replay and self.manifest["dataset_sha256"] != self.replay["source_sha256"]["dataset.json"]:
            raise ValueError("Replay dataset differs from the frozen source")
        indices = list(range(len(self.dataset["points"])))
        random.Random(self.args.seed).shuffle(indices)
        if self.args.limit:
            indices = indices[:self.args.limit]
        self.points = [self.dataset["points"][i] for i in indices]
        self.selected = set(indices)
        if self.replay:
            replay_ids = set(self.replay["sample_ids"])
            for row in source_records(self.replay):
                if row["sample_id"] not in replay_ids:
                    self.records[self.replay["model"]][row["sample_id"]] = row
                    if row.get("returned_model"):
                        self.returned_models[self.replay["model"]].add(row["returned_model"])
        for entry in read_jsonl(self.run / "attempts.jsonl", recover_tail=True):
            key = (entry["model"], entry["sample_id"])
            self.attempts[key] = max(self.attempts[key], entry["attempt"])
            self.peak_inflight = max(self.peak_inflight, entry.get("inflight", 0))
            if entry["event"] == "end":
                self.http_counts[str(entry["http_status"])] += 1
                self.previous_retries += bool(entry.get("retry"))
                if entry.get("error") == "ReadTimeout":
                    self.read_timeouts.add(key)
                    self.timeout_attempts[key] += 1
        for model in self.models:
            for row in read_jsonl(self.run / (model.name + ".jsonl"), recover_tail=True):
                if self.replay and row["sample_id"] not in replay_ids:
                    raise ValueError("Replay result outside the frozen selection")
                if row["sample_id"] in self.records[model.name]:
                    raise ValueError("Duplicate terminal result in frozen evidence")
                self.records[model.name][row["sample_id"]] = row
                if row.get("returned_model"):
                    self.returned_models[model.name].add(row["returned_model"])
            self.handles[model.name] = (self.run / (model.name + ".jsonl")).open("a", buffering=1)
        self.handles["attempts"] = (self.run / "attempts.jsonl").open("a", buffering=1)
        self.handles["lifecycle"] = (self.run / "lifecycle.jsonl").open("a", buffering=1)
        revisions = self.manifest.get("execution_revisions", [])
        self.log("lifecycle", {"event": "start_or_resume", "time": now(), "pid": os.getpid(),
                               "read_timeout_policy": {"base_seconds": self.args.timeout,
                                                       "after_read_timeout_seconds": self.args.timeout * 2},
                               "execution_revision": revisions[-1]["source_directory"] if revisions else "source"})

    def log(self, handle: str, value: dict):
        self.handles[handle].write(json.dumps(redact(value, self.secrets), ensure_ascii=False,
                                            separators=(",", ":")) + "\n")

    def save(self, model, point, attempt, **kwargs):
        row = {"sample_id": point["id"], "model": model.name, "attempts": attempt,
               "time": now(), "label": None, **kwargs}
        if row["status"] != "infra_error":
            projected = self.project_answer(row, model.wire)
            row.update(projected)
        row = redact(row, self.secrets)
        self.log(model.name, row)
        self.records[model.name][point["id"]] = row
        if row.get("returned_model"):
            self.returned_models[model.name].add(row["returned_model"])

    async def sleep(self, seconds):
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def sample(self, client, model, point):
        key = (model.name, point["id"])
        if self.attempts[key] >= self.args.max_retries + 1:
            _, payload = model.request(point["prompt"])
            self.save(model, point, self.attempts[key], status="infra_error", error="attempt_budget_exhausted",
                      request=payload, response=None)
            return
        headers, payload = model.request(point["prompt"])
        while not self.stop.is_set() and model.name not in self.circuits:
            self.attempts[key] += 1
            attempt = self.attempts[key]
            started = time.monotonic()
            body = None
            http_status = 0
            error = None
            retry_after = None
            self.inflight += 1
            self.peak_inflight = max(self.peak_inflight, self.inflight)
            timeout = self.args.timeout * (2 if key in self.read_timeouts else 1)
            self.log("attempts", {"event": "start", "time": now(), "model": model.name,
                                  "sample_id": point["id"], "attempt": attempt, "inflight": self.inflight,
                                  "timeout_seconds": timeout})
            try:
                response = await asyncio.wait_for(
                    client.post(model.url, headers=headers, json=payload,
                                timeout=httpx.Timeout(timeout, connect=min(30, timeout))),
                    timeout=timeout)
                http_status = response.status_code
                retry_after = response.headers.get("retry-after")
                try:
                    body = response.json()
                except ValueError:
                    error = "non_json_response"
                if not isinstance(body, dict):
                    body = None
                    error = "non_object_response"
                if body and body.get("error"):
                    error = body["error"]
            except (httpx.TransportError, httpx.TimeoutException, TimeoutError) as exc:
                error = type(exc).__name__
                if isinstance(exc, (httpx.ReadTimeout, TimeoutError)):
                    self.read_timeouts.add(key)
                    error = "ReadTimeout"
            finally:
                self.inflight -= 1
            latency = time.monotonic() - started
            success = 200 <= http_status < 300 and body is not None and not error
            error_type = str(error.get("type", error.get("code", ""))) if isinstance(error, dict) else str(error or "")
            retryable = (http_status in RETRYABLE or http_status == 0
                         or error_type in {"rate_limit_error", "rate_limit_exceeded", "overloaded_error"}
                         or (200 <= http_status < 300 and body is None))
            retry = not success and retryable and attempt <= self.args.max_retries
            timeout_budget = getattr(self.args, "max_timeout_retries", None)
            if error == "ReadTimeout" and timeout_budget is not None:
                self.timeout_attempts[key] += 1
                retry = retry and self.timeout_attempts[key] <= timeout_budget
            delay = retry_delay(attempt, retry_after) if retry else None
            self.http_counts[str(http_status)] += 1
            self.log("attempts", {"event": "end", "time": now(), "model": model.name,
                                  "sample_id": point["id"], "attempt": attempt,
                                  "http_status": http_status, "latency_seconds": latency,
                                  "retry": retry, "retry_delay_seconds": delay,
                                  "error": error, "response": body if not success else None})
            if success:
                text, complete = extract_answer(model.wire, body)
                label = parse_label(text) if complete else None
                self.save(model, point, attempt, status="ok" if label is not None else "invalid",
                          label=label, text=text, complete_response=complete,
                          latency_seconds=latency, http_status=http_status,
                          returned_model=body.get("model"), usage=usage_counts(body.get("usage", {})),
                          request=payload, response=body)
                return
            if retry:
                self.retries += 1
                await self.sleep(delay)
                continue
            self.save(model, point, attempt, status="infra_error", error=error or f"HTTP {http_status}",
                      http_status=http_status, latency_seconds=latency, request=payload, response=body)
            # A gateway filter applies to this coordinate, not to the model's credentials.
            # Preserve its unknown result and continue other coordinates without retrying it.
            if http_status in {400, 401, 403, 404} and not is_content_filter(error):
                self.circuits[model.name] = f"permanent HTTP {http_status}"
            return

    def snapshot(self):
        models = []
        for model in self.models:
            records = self.records[model.name]
            score = self.score_records(self.points, records)
            pixels = ["5"] * len(self.dataset["points"])
            for idx in self.selected:
                pixels[idx] = "4"
            details = {}
            usage = Counter()
            latencies = []
            for idx, r in records.items():
                pixels[idx] = str(r["label"]) if r["status"] == "ok" else ("2" if r["status"] == "invalid" else "3")
                details[str(idx)] = {k: v for k, v in r.items() if k not in {"response", "request"}}
                usage.update(r.get("usage", {}))
                if "latency_seconds" in r:
                    latencies.append(r["latency_seconds"])
            latencies.sort()
            models.append({**model.public(), "returned_models": sorted(self.returned_models[model.name]),
                           "metrics": score, "pixels": "".join(pixels), "usage": dict(usage),
                           "attempts": sum(v for (n, _), v in self.attempts.items() if n == model.name),
                           "latency_p50": latencies[len(latencies) // 2] if latencies else None,
                           "latency_p95": latencies[min(len(latencies) - 1, int(len(latencies) * .95))] if latencies else None,
                           "details_url": model.name + ".details.json", "circuit": self.circuits.get(model.name)})
            if self.replay:
                completed = [records[idx] for idx in self.replay["sample_ids"] if idx in records]
                models[-1]["replay"] = {**self.replay, "selected_count": len(self.replay["sample_ids"]),
                                         "completed": len(completed), "reused": len(records) - len(completed),
                                         "pending": len(self.replay["sample_ids"]) - len(completed)}
            atomic_json(self.export / (model.name + ".details.json"), details)
        data = {"run_id": self.run.name, "created_at": self.manifest["created_at"], "updated_at": now(),
                "state": self.state, "config": self.config, "source": self.source_url,
                "elapsed_seconds": (datetime.now(timezone.utc) - datetime.fromisoformat(self.manifest["created_at"])).total_seconds(),
                "inflight": self.inflight, "peak_inflight": self.peak_inflight,
                "retries": self.previous_retries + self.retries, "http_counts": dict(self.http_counts),
                "dataset_sha256": self.manifest["dataset_sha256"],
                "grid": {k: v for k, v in self.dataset.items() if k != "points"},
                "truth": "".join(str(p["truth"]) for p in self.dataset["points"]), "models": models}
        atomic_json(self.run / "summary.json", data)
        atomic_json(self.export / "data.json", data)
        done = sum(len(x) for x in self.records.values())
        print(json.dumps({"time": now(), "state": self.state, "done": done,
                          "total": len(self.points) * len(self.models), "inflight": self.inflight,
                          "peak": self.peak_inflight, "retries": data["retries"],
                          "circuits": self.circuits}), flush=True)
        for handle in self.handles.values():
            handle.flush()
            os.fsync(handle.fileno())

    async def reporter(self):
        while not self.stop.is_set():
            if (self.run / "STOP_REQUESTED.json").exists():
                self.stop.set()
                break
            self.snapshot()
            await self.sleep(10)

    async def execute(self):
        self.prepare()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, self.stop.set)
        queues = {model.name: asyncio.Queue() for model in self.models}
        for model in self.models:
            for point in self.points:
                if point["id"] not in self.records[model.name]:
                    queues[model.name].put_nowait(point)

        async def worker(client, model):
            queue = queues[model.name]
            while not self.stop.is_set() and model.name not in self.circuits:
                try:
                    point = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                if (self.run / "STOP_REQUESTED.json").exists():
                    self.stop.set()
                    return
                await self.sample(client, model, point)
                queue.task_done()

        async def model_workers(model):
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.args.timeout, connect=30),
                                         limits=httpx.Limits(max_connections=self.args.concurrency,
                                                             max_keepalive_connections=self.args.concurrency),
                                         trust_env=False) as client:
                await asyncio.gather(*(worker(client, model) for _ in range(self.args.concurrency)))

        reporter = asyncio.create_task(self.reporter())
        try:
            await asyncio.gather(*(model_workers(model) for model in self.models))
            self.state = "stopped" if self.stop.is_set() else (
                "complete" if all(metrics(self.points, r)["pending"] == 0 for r in self.records.values()) else "incomplete")
        except BaseException:
            self.state = "failed"
            raise
        finally:
            self.stop.set()
            await reporter
            self.log("lifecycle", {"event": self.state, "time": now()})
            self.snapshot()
            for h in self.handles.values():
                h.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--models", nargs="+", required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--export-dir", type=Path, help="Local snapshots; defaults to RUN_DIR/snapshot")
    p.add_argument("--concurrency", type=int, default=512, help="Maximum concurrent requests PER MODEL")
    p.add_argument("--max-retries", type=int, default=1000)
    p.add_argument("--timeout", type=float, default=180)
    p.add_argument("--seed", type=int, default=20261009)
    p.add_argument("--limit", type=int, default=0, help="0 = all 16,200 points per model")
    p.add_argument("--max-output-tokens", type=int, help="Override the per-model config (default 65,536)")
    p.add_argument("--replay-truncated-from", type=Path,
                   help="Reuse a verified source and resample only output-cap truncations")
    p.add_argument("--resume-revision", help="Record a justified execution-code repair while preserving inputs, prior results and retry budgets")
    args = p.parse_args(argv)
    args.export_dir = args.export_dir or args.run_dir / "snapshot"
    if not 1 <= args.concurrency <= 512 or not 0 <= args.max_retries <= 1000:
        p.error("concurrency must be 1..512; max-retries must be 0..1000")
    if not 0 <= args.limit <= 16200 or args.timeout <= 0 or len(set(args.models)) != len(args.models) or (args.max_output_tokens is not None and args.max_output_tokens < 1):
        p.error("invalid limit, timeout, or duplicate model names")
    asyncio.run(Runner(args).execute())


if __name__ == "__main__":
    main()

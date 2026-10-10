import asyncio
import json
import shutil
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from land_water_bench.config import ModelConfig, load_models, redact
from land_water_bench.core import PROMPT, extract_answer, make_grid, metrics, parse_label, read_jsonl
from land_water_bench.runner import Runner, retry_delay


def test_grid_has_unique_centers_and_symmetric_area():
    grid = make_grid()
    assert len(grid) == len({(p["lat"], p["lon"]) for p in grid}) == 16200
    assert (grid[0]["lat"], grid[0]["lon"]) == (89, -179)
    assert (grid[-1]["lat"], grid[-1]["lon"]) == (-89, 179)
    assert sum(p["weight"] for p in grid if p["lat"] > 0) == pytest.approx(
        sum(p["weight"] for p in grid if p["lat"] < 0))


def test_parse_does_not_cherry_pick_words_in_prose_or_thinking():
    assert parse_label(" Water\n") == 0
    assert parse_label("LAND") == 1
    for text in ["Land or Water", "Probably land", "Water.", "", "**Land**"]:
        assert parse_label(text) is None
    text, complete = extract_answer("messages", {
        "content": [{"type": "thinking", "thinking": "Land"}, {"type": "text", "text": "Water"}],
        "stop_reason": "end_turn"})
    assert text == "Water" and complete
    assert not extract_answer("responses", {"status": "incomplete", "output": []})[1]


def test_partial_and_infra_are_not_zero_scores():
    points = [{"id": 0, "weight": 1, "truth": 1}, {"id": 1, "weight": .5, "truth": 0}]
    rows = {0: {"status": "ok", "label": 1}}
    result = metrics(points, rows)
    assert result["weighted_accuracy"] is None
    assert result["observed_weighted_accuracy"] == 1
    assert result["accuracy_lower_bound"] == pytest.approx(2 / 3)
    rows[1] = {"status": "infra_error"}
    assert metrics(points, rows)["weighted_accuracy"] is None
    rows[1] = {"status": "invalid"}
    assert metrics(points, rows)["weighted_accuracy"] == pytest.approx(2 / 3)
    rows[1] = {"status": "ok", "label": 0}
    assert metrics(points, rows)["weighted_accuracy"] == 1


def test_request_is_independent_and_has_no_tools():
    for wire in ["messages", "responses"]:
        m = ModelConfig("m", "actual", wire, "https://invalid.test", "private", {"effort": "low"})
        headers, body = m.request("Latitude: 1, Longitude: 2. Land or Water?")
        assert "tools" not in body and "system" not in body and "instructions" not in body
        assert "previous_response_id" not in body
        assert "private" not in json.dumps(m.public())
        assert "private" not in json.dumps(redact({"x": headers, "y": "error private"}, [m.key]))


def test_retry_after_and_truncated_tail(tmp_path):
    class Fixed:
        def uniform(self, a, b):
            return a
    assert retry_delay(1, "17", Fixed()) == 17
    path = tmp_path / "records.jsonl"
    path.write_bytes(b'{"id":1}\n{"id":')
    assert read_jsonl(path, recover_tail=True) == [{"id": 1}]
    assert path.read_bytes() == b'{"id":1}\n'
    assert len(list(tmp_path.glob("*.tail-*"))) == 1
    path.write_bytes(b'bad\n{"id":2}\n')
    with pytest.raises(ValueError):
        read_jsonl(path, recover_tail=True)


def sample_runner(tmp_path, max_retries=1000):
    r = object.__new__(Runner)
    r.args = SimpleNamespace(max_retries=max_retries, timeout=180)
    r.secrets = ["private"]
    r.records = {"m": {}}
    r.attempts = Counter()
    r.read_timeouts = set()
    r.http_counts = Counter()
    r.returned_models = {"m": set()}
    r.stop = asyncio.Event()
    r.inflight = r.peak_inflight = r.retries = 0
    r.circuits = {}
    r.handles = {name: (tmp_path / (name + ".jsonl")).open("w", buffering=1) for name in ["m", "attempts"]}
    async def fast_sleep(seconds):
        await asyncio.sleep(0)
    r.sleep = fast_sleep
    return r, ModelConfig("m", "actual", "messages", "https://model.test/v1/messages", "private", {})


@pytest.mark.asyncio
async def test_1000_retries_are_bounded_and_resumption_preserves_budget(tmp_path):
    runner, model = sample_runner(tmp_path)
    calls = 0
    async def limited(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {"type": "rate_limit_error"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as client:
        await runner.sample(client, model, {"id": 0, "prompt": "Land or Water?"})
        assert calls == 1001 and runner.retries == 1000
        assert runner.records["m"][0]["status"] == "infra_error"
        await runner.sample(client, model, {"id": 0, "prompt": "Land or Water?"})
        assert calls == 1001
    for f in runner.handles.values():
        f.close()


@pytest.mark.asyncio
async def test_rate_limit_then_success_and_invalid_answers_are_not_retried(tmp_path):
    runner, model = sample_runner(tmp_path)
    responses = [httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"type": "rate_limit_error"}}),
                 httpx.Response(200, json={"model": "actual", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Land"}]}),
                 httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "Land or Water"}]})]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0))) as client:
        await runner.sample(client, model, {"id": 0, "prompt": "test"})
        await runner.sample(client, model, {"id": 1, "prompt": "test"})
    assert runner.records["m"][0]["label"] == 1
    assert runner.records["m"][0]["attempts"] == 2
    assert runner.records["m"][1]["status"] == "invalid"
    assert runner.records["m"][1]["attempts"] == 1
    assert not responses
    for f in runner.handles.values():
        f.close()


@pytest.mark.asyncio
async def test_permanent_auth_error_opens_model_circuit(tmp_path):
    runner, model = sample_runner(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "invalid key"}))) as client:
        await runner.sample(client, model, {"id": 0, "prompt": "test"})
        await runner.sample(client, model, {"id": 1, "prompt": "test"})
    assert runner.attempts[("m", 1)] == 0
    assert runner.records["m"][0]["status"] == "infra_error"
    assert runner.circuits == {"m": "permanent HTTP 401"}
    for f in runner.handles.values():
        f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [{"type": "content_filter", "message": "filtered"},
                                   {"type": "api_error", "message": "InternalError.Algo.DataInspectionFailed: Output data may contain inappropriate content."}])
async def test_coordinate_filter_is_not_retried_and_does_not_stop_other_points(tmp_path, error):
    runner, model = sample_runner(tmp_path)
    responses = [httpx.Response(400, json={"error": error}),
                 httpx.Response(200, json={"model": "actual", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Land"}]})]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0))) as client:
        await runner.sample(client, model, {"id": 0, "prompt": "test"})
        await runner.sample(client, model, {"id": 1, "prompt": "test"})
    assert runner.records["m"][0]["status"] == "infra_error"
    assert runner.records["m"][0]["attempts"] == 1
    assert runner.records["m"][1]["status"] == "ok"
    assert not runner.circuits and runner.retries == 0
    for f in runner.handles.values():
        f.close()


def integration_args(tmp_path, monkeypatch, *, limit=3, concurrency=1):
    from land_water_bench import runner as module

    points = make_grid()
    for point in points:
        point.update(truth=point["id"] % 2, prompt=PROMPT.format(**point))
    monkeypatch.setattr(module, "make_dataset", lambda: {"points": points, "width": 180, "height": 90})
    config = tmp_path / "config.yaml"
    monkeypatch.setenv("TEST_API_KEY", "test-private-credential")
    config.write_text(yaml.safe_dump({"models": {"m": {
        "model": "test-model", "wire": "messages", "url": "https://model.test/v1/messages",
        "api_key_env": "TEST_API_KEY"}}}))
    return SimpleNamespace(config=config, models=["m"], run_dir=tmp_path / "run", export_dir=tmp_path / "export",
                           concurrency=concurrency, max_retries=1000, timeout=10, seed=20261009, limit=limit)


@pytest.mark.asyncio
async def test_stop_and_resume_preserve_answers_and_attempt_budget(tmp_path, monkeypatch):
    from land_water_bench import runner as module

    args = integration_args(tmp_path, monkeypatch)
    actual_client = httpx.AsyncClient
    calls = 0
    first = Runner(args)

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": {"type": "rate_limit_error"}})
        if calls == 3:
            first.stop.set()
        return httpx.Response(200, json={"model": "test-model", "stop_reason": "end_turn",
                                        "content": [{"type": "text", "text": "Water"}]})

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(module, "retry_delay", lambda *args: 0)
    await first.execute()
    first.lock.close()
    original = (args.run_dir / "m.jsonl").read_bytes()
    assert first.state == "stopped" and len(first.records["m"]) == 2
    second = Runner(args)
    await second.execute()
    second.lock.close()
    assert second.state == "complete" and calls == 4
    assert (args.run_dir / "m.jsonl").read_bytes().startswith(original)
    assert second.previous_retries == 1
    assert sum(second.attempts.values()) == 4
    assert "test-private-credential" not in (args.run_dir / "config.json").read_text()


@pytest.mark.asyncio
async def test_read_timeout_retry_extension_survives_resume_without_changing_payload(tmp_path, monkeypatch):
    from land_water_bench import runner as module

    args = integration_args(tmp_path, monkeypatch, limit=1)
    args.timeout = 1800
    args.max_output_tokens = 65536
    actual_client = httpx.AsyncClient
    first = Runner(args)
    requests = []

    async def handler(request):
        requests.append((json.loads(request.content), request.extensions["timeout"]["read"]))
        if len(requests) == 1:
            first.stop.set()
            raise httpx.ReadTimeout("long generation", request=request)
        return httpx.Response(200, json={"model": "test-model", "stop_reason": "end_turn",
                                        "content": [{"type": "text", "text": "**Water**"}]})

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    await first.execute()
    first.lock.close()
    assert first.state == "stopped" and not first.records["m"]
    second = Runner(args)
    await second.execute()
    second.lock.close()
    assert [timeout for _, timeout in requests] == [1800, 3600]
    assert requests[0][0] == requests[1][0] and requests[1][0]["max_tokens"] == 65536
    assert second.previous_retries == 1 and sum(second.attempts.values()) == 2
    assert next(iter(second.records["m"].values()))["status"] == "ok"
    assert next(iter(second.records["m"].values()))["extraction"]["rule"] == "final_line"
    starts = [e for e in read_jsonl(args.run_dir / "attempts.jsonl") if e["event"] == "start"]
    assert [e["timeout_seconds"] for e in starts] == [1800, 3600]


@pytest.mark.asyncio
async def test_512_workers_never_exceed_512_inflight_requests(tmp_path, monkeypatch):
    from land_water_bench import runner as module

    args = integration_args(tmp_path, monkeypatch, limit=600, concurrency=512)
    actual_client = httpx.AsyncClient
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(.02)
        active -= 1
        return httpx.Response(200, json={"model": "test-model", "stop_reason": "end_turn",
                                        "content": [{"type": "text", "text": "Water"}]})

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    task = Runner(args)
    await task.execute()
    task.lock.close()
    assert task.state == "complete"
    assert peak == task.peak_inflight == 512
    assert len(task.records["m"]) == 600


def test_execution_revision_preserves_original_source_and_inputs(tmp_path, monkeypatch):
    from land_water_bench import runner as module

    args = integration_args(tmp_path, monkeypatch)
    first = Runner(args)
    first.prepare()
    for handle in first.handles.values():
        handle.close()
    first.lock.close()
    original = (args.run_dir / "source/runner.py").read_bytes()
    original_config = (args.run_dir / "config.json").read_bytes()
    alternate = tmp_path / "new-code"
    alternate.mkdir()
    for name in module.SOURCE_FILES:
        shutil.copy2(module.ROOT / name, alternate / name)
    with (alternate / "runner.py").open("a") as handle:
        handle.write("\n# recorded transport-only repair\n")
    monkeypatch.setattr(module, "ROOT", alternate)
    refused = Runner(args)
    with pytest.raises(ValueError, match="Runner source changed"):
        refused.prepare()
    refused.lock.close()
    args.resume_revision = "Repair a transport classifier; preserve prompts and scores"
    repaired = Runner(args)
    repaired.prepare()
    assert (args.run_dir / "source/runner.py").read_bytes() == original
    assert (args.run_dir / "config.json").read_bytes() == original_config
    manifest = json.loads((args.run_dir / "manifest.json").read_text())
    revision = manifest["execution_revisions"][-1]
    assert revision["reason"] == args.resume_revision
    assert (args.run_dir / revision["source_directory"] / "manifest-before.json").exists()
    assert (args.run_dir / revision["source_directory"] / "runner.py").read_bytes().endswith(b"# recorded transport-only repair\n")
    for handle in repaired.handles.values():
        handle.close()
    repaired.lock.close()

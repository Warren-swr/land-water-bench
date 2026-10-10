import asyncio
from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from land_water_bench import runner as module
from land_water_bench.config import load_models
from land_water_bench.core import make_dataset
from land_water_bench.results import export_run, read, verify_results, verify_run
from land_water_bench.runner import Runner

ROOT = Path(__file__).resolve().parents[1]


def config_args(tmp_path, monkeypatch, *, models=("alpha",), limit=0, concurrency=8, cap=4096):
    monkeypatch.setenv("TEST_KEY", "test-credential-not-for-publication")
    monkeypatch.setenv("TEST_URL", "https://private-endpoint.test/v1/messages")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"models": {name: {
        "model": name, "wire": "messages", "url_env": "TEST_URL", "api_key_env": "TEST_KEY",
        "max_output_tokens": cap,
    } for name in models}}))
    return SimpleNamespace(config=path, models=list(models), run_dir=tmp_path / "run", export_dir=tmp_path / "snapshot",
                           concurrency=concurrency, max_retries=3, timeout=10, seed=20261009, limit=limit)


def mock_client(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(module, "retry_delay", lambda *args: 0)


def answer(text="Water", stop="end_turn"):
    return httpx.Response(200, json={"stop_reason": stop, "content": [{"type": "text", "text": text}]})


def test_only_explicit_environment_configuration_is_loaded(tmp_path, monkeypatch):
    args = config_args(tmp_path, monkeypatch)
    config = load_models(args.config, args.models)[0]
    assert config.max_tokens == 4096 and config.model == "alpha"
    assert "test-credential" not in repr(config) and "private-endpoint" not in repr(config)
    for name in ("../escape", "reference", "attempts"):
        with pytest.raises(ValueError, match="name"):
            load_models(args.config, [name])
    raw = yaml.safe_load(args.config.read_text())
    raw["models"]["alpha"]["api_key"] = "never-inline"
    args.config.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="fields"):
        load_models(args.config, args.models)


@pytest.mark.asyncio
async def test_512_concurrency_is_per_model_not_shared(tmp_path, monkeypatch):
    args = config_args(tmp_path, monkeypatch, models=("alpha", "beta"), limit=520, concurrency=512)
    active, peaks = Counter(), Counter()
    both_full = asyncio.Event()

    async def handler(request):
        name = json.loads(request.content)["model"]
        active[name] += 1
        peaks[name] = max(peaks[name], active[name])
        if active["alpha"] == active["beta"] == 512:
            both_full.set()
        await asyncio.wait_for(both_full.wait(), 5)
        await asyncio.sleep(0)
        active[name] -= 1
        return answer()

    mock_client(monkeypatch, handler)
    task = Runner(args)
    await task.execute()
    task.lock.close()
    assert peaks == {"alpha": 512, "beta": 512}
    assert task.peak_inflight == 1024
    assert task.state == "complete" and all(len(rows) == 520 for rows in task.records.values())


@pytest.mark.asyncio
async def test_total_deadline_retries_slow_response(tmp_path, monkeypatch):
    args = config_args(tmp_path, monkeypatch, limit=1, concurrency=1)
    args.timeout = 0.03
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(1)
        return answer("Explanation without a contrary label.\n\n**Water**")

    mock_client(monkeypatch, handler)
    task = Runner(args)
    await task.execute()
    task.lock.close()
    assert calls == 2
    row = next(iter(task.records["alpha"].values()))
    assert row["status"] == "ok" and row["label"] == 0
    assert task.read_timeouts and task.retries == 1


@pytest.mark.asyncio
async def test_full_run_seal_replay_and_public_export_preserve_evidence(tmp_path, monkeypatch):
    args = config_args(tmp_path, monkeypatch, concurrency=64)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return answer("Water", "max_tokens")
        if calls == 2:
            return answer("Land or Water")
        if calls == 3:
            return httpx.Response(400, json={"error": {"type": "content_filter"}})
        return answer("**Water**")

    mock_client(monkeypatch, handler)
    original = Runner(args)
    await original.execute()
    original.lock.close()
    proof = verify_run(args.run_dir, write=True)
    assert proof["full_grid"] and proof["models"][0]["invalid"] == 2
    assert proof["models"][0]["excluded_unresolved"] == 1
    frozen = (args.run_dir / "alpha.jsonl").read_bytes()
    with pytest.raises(ValueError, match="sealed"):
        Runner(args).prepare()
    replay_args = SimpleNamespace(**{**vars(args), "run_dir": tmp_path / "replay", "export_dir": tmp_path / "replay-snapshot",
                                    "max_output_tokens": 65536, "replay_truncated_from": args.run_dir})
    replay_calls = []

    def replay_handler(request):
        replay_calls.append(json.loads(request.content))
        return answer("Explanation.\nFinal answer: Water")

    # Replace the existing factory without wrapping it a second time.
    monkeypatch.undo()
    monkeypatch.setenv("TEST_KEY", "test-credential-not-for-publication")
    monkeypatch.setenv("TEST_URL", "https://private-endpoint.test/v1/messages")
    mock_client(monkeypatch, replay_handler)
    replay = Runner(replay_args)
    await replay.execute()
    replay.lock.close()
    assert len(replay_calls) == 1 and replay_calls[0]["max_tokens"] == 65536
    proof = verify_run(replay_args.run_dir, write=True)
    assert proof["models"][0]["invalid"] == 1 and proof["models"][0]["excluded_unresolved"] == 1
    assert (args.run_dir / "alpha.jsonl").read_bytes() == frozen
    output = tmp_path / "public"
    checked = export_run(replay_args.run_dir, output)
    assert checked["records"] == 16200
    for path in output.iterdir():
        text = path.read_text()
        assert "test-credential-not-for-publication" not in text
        assert "private-endpoint.test" not in text
        assert str(tmp_path) not in text
    rows = read(output / "alpha.details.json")
    assert sum(row["status"] == "invalid" for row in rows.values()) == 1
    rows["0"]["text"] = "tampered"
    (output / "alpha.details.json").write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="Checksum"):
        verify_results(output)


def test_bundled_result_extraction_reference_and_every_score():
    proof = verify_results(ROOT / "results/2026-10-10")
    assert proof["records"] == 194400 and len(proof["models"]) == 12
    assert sum(m["invalid"] for m in proof["models"]) == 320
    assert sum(m["excluded_unresolved"] for m in proof["models"]) == 3
    assert make_dataset()["reference"]["name"] == "GSHHG"

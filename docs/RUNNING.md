# Running and adding results

## Configuration

`configs/models.example.yaml` demonstrates both supported wire formats. A model entry has:

| Field | Meaning |
| --- | --- |
| mapping key | Safe public experiment alias; used in filenames |
| `model` or `model_env` | Literal model ID or environment variable containing it |
| `wire` | `responses` or `messages` |
| `url` or `url_env` | Complete endpoint URL, or variable containing it |
| `api_key_env` | Name of the environment variable containing the credential |
| `reasoning` | Optional provider-supported reasoning/thinking object |
| `max_output_tokens` | Per-model cap; default 65,536 |

Exactly one field from each `or` pair is required. Unknown configuration keys are rejected. Responses sends bearer authentication; Messages sends `x-api-key` and `anthropic-version: 2023-06-01`. Arbitrary agent settings, tools, prompts, and headers are not accepted. For custom authentication or wire formats, add and test a dedicated adapter.

The endpoint's full route is explicit; the runner does not guess or append `/v1`. Example public routes are `https://api.openai.com/v1/responses` and `https://api.anthropic.com/v1/messages`. Set the model ID and parameters to something your endpoint supports. The historical aliases in the results are not promised to work on these endpoints.

## Retry and interruption behavior

The default concurrency is 512 **per model**. A request initially has a 180-second total wall-clock and HTTP read deadline. After a timeout on that coordinate, subsequent attempts have twice that deadline; the retry-timeout state survives resume. `--timeout` sets the initial limit.

Network failures, non-JSON 2xx responses, HTTP 408/425/429/500/502/503/504/529, and recognized overloaded/rate-limit errors may retry. Positive `Retry-After` values are honored; otherwise exponential backoff with jitter is used. `--max-retries 1000` means at most 1,001 attempts including the first; resume does not reset this budget. Completed wrong or invalid answers are terminal. A content-filter error is unresolved and does not trigger blind retries. Other permanent 400/401/403/404 errors stop scheduling for that model.

A run directory is locked against duplicate runners. Input settings, endpoint hashes, dataset hash, execution source, dependencies, raw responses, and per-attempt records are saved. Keys and endpoint URLs are redacted from saved envelopes/errors; source configuration is not copied. Treat raw runs as private until inspected, as providers can return arbitrary metadata.

Use the same command to resume an unsealed run. Different settings or endpoints are rejected. A code repair requires `--resume-revision 'reason'`; it records a new source snapshot while retaining the original. Do not use this to change sampling or scoring mid-run. `verify-run --seal` freezes a terminal run and prevents further resume.

## Selective output-cap replay

A replay is a new experiment with explicit provenance, not a silent change to a frozen score. Only responses that the provider explicitly stopped at its output cap qualify. Completed format errors and wrong answers are reused unchanged.

```bash
lwb verify-run --run-dir runs/original --seal
lwb run --config configs/local.yaml --models responses-model \
  --run-dir runs/replay-64k --max-output-tokens 65536 \
  --replay-truncated-from runs/original
lwb verify-run --run-dir runs/replay-64k --seal
lwb export-run --run-dir runs/replay-64k --output results/replay-64k
```

The original must be full-grid, sealed, and verified. A replay accepts one model, the same identity/reasoning settings, and a strictly larger output cap. It does not modify its source. There must be at least one qualifying truncation. Keep the source run available while verifying/exporting the replay.

## Output contracts

Raw runs contain `config.json`, `manifest.json`, `dataset.json`, source snapshots, `attempts.jsonl`, one model `.jsonl`, `summary.json`, and local `snapshot/` exports. These are ignored by Git.

A public collection contains `data.json`, one `<alias>.details.json` per model, `reference.details.json`, `provenance.json`, `RESULTS.md`, `verification.json`, and `FILES.sha256`. Records use statuses `ok`, `invalid`, or `infra_error`; labels are `1` for land, `0` for water, and `null` for invalid/unresolved. Prediction strings use `0/1`, `2` for invalid, and `3` for unresolved.

`lwb verify` checks every checksum, ID, reference label, extracted answer, prediction pixel, score, and rank. It also recomputes the primary score independently with `math.fsum` and freshly calculated latitude weights. It never contacts a model endpoint.

To extend a collection, export into a fresh directory with `--base EXISTING_COLLECTION`. Duplicate aliases are rejected. Choose a distinct alias for a new configuration. Point `lwb build --results` and the Pages workflow at the collection you intend to publish. The bundled browser acceptance script is a regression suite for the included 12-model release; update its fixture expectations when intentionally changing that release.

# Land / Water Bench

**One coordinate. One word. A model's map of the world.**

[![Checks](https://github.com/Warren-swr/land-water-bench/actions/workflows/checks.yml/badge.svg)](https://github.com/Warren-swr/land-water-bench/actions/workflows/checks.yml)
[![Pages](https://github.com/Warren-swr/land-water-bench/actions/workflows/pages.yml/badge.svg)](https://github.com/Warren-swr/land-water-bench/actions/workflows/pages.yml)
[![Code: MIT](https://img.shields.io/badge/code-MIT-blue)](LICENSE)

[Interactive leaderboard](https://warren-swr.github.io/land-water-bench/) · [Results](results/2026-10-10/RESULTS.md) · [Protocol](docs/PROTOCOL.md) · [中文说明](docs/README.zh-CN.md)

A reproducible, coordinate-only benchmark: ask a language model whether each of **16,200 points** on a global 2° grid is land or water. Each point is an independent API request. No agent runtime, tools, browsing, images, map context, or conversation history are supplied.

Inspired by [Celeste's experiment](https://x.com/celestepoasts/status/2103232383139057950). This is an independent reproduction with an explicit sampling grid and geographic reference; scores are not directly comparable to the original post.

![The light-themed leaderboard and geographic comparison](docs/preview.png)

## What's included

- A Python runner for **Responses** and **Messages** API-compatible endpoints, with per-model concurrency, bounded retries, timeouts, checkpointing, and resumable evidence.
- A frozen **GSHHG 2.3.7** reference, checksum-pinned source downloads, and an independent geometric cross-check.
- **12 model result sets / 194,400 final responses**, including individual answer bodies, invalid responses, unresolved requests, and exact scoring metadata.
- A static, dependency-free leaderboard: interactive globe, model filters, side-by-side maps, error/difference views, per-coordinate answers, and CSV download.
- Automated tests, offline score verification, and GitHub Pages deployment. CI and site builds make **zero model API requests**.

## Quick start

Python 3.11+ on Linux or macOS. The runner uses POSIX file locks and signal handling; Windows users can use WSL. Viewing the published site requires only a browser.

```bash
git clone https://github.com/Warren-swr/land-water-bench.git
cd land-water-bench
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

lwb verify
lwb build
lwb serve --directory dist --port 8732
```

Open <http://localhost:8732>. `lwb build` refuses an existing output directory; use a new `--output` directory or remove only your previous generated `dist/` before rebuilding. No API key or geographic-source download is needed to verify the included results or build the site.

## Run a model

1. Copy [configs/models.example.yaml](configs/models.example.yaml) to the ignored `configs/local.yaml`.
2. Set the API key, **full endpoint URL**, and actual available model ID through the environment variables named in that file. The program does not load `.env` files automatically.
3. Start with a small smoke run, then use a **different run directory** for the full grid.

```bash
# After configuring LWB_RESPONSES_URL, LWB_RESPONSES_API_KEY, and LWB_RESPONSES_MODEL:
lwb run --config configs/local.yaml --models responses-model \
  --run-dir runs/smoke --limit 8 --concurrency 2 --max-retries 3

# Full benchmark: 512 simultaneous requests PER MODEL; up to 1,000 retries per point.
lwb run --config configs/local.yaml --models responses-model messages-model \
  --run-dir runs/experiment-001 --concurrency 512 --max-retries 1000
```

Two models at `--concurrency 512` may issue **1,024 simultaneous requests** in total. Retry backoff occupies that model's worker slots. These limits can generate substantial API usage; use settings appropriate for your account. The default output cap is 65,536 tokens per request, configurable per model; providers may reject unsupported caps. Reasoning/thinking settings are forwarded exactly as configured, and must also be supported by the endpoint.

Run the **same command and configuration** to resume an interrupted, unsealed run. Previous terminal answers and per-point attempt counts are retained. `Ctrl+C`, `SIGTERM`, or creating `RUN_DIR/STOP_REQUESTED.json` stops further scheduling; current requests finish within their configured timeout. Remove the stop file before an intentional resume.

```bash
lwb verify-run --run-dir runs/experiment-001 --seal
lwb export-run --run-dir runs/experiment-001 --output results/experiment-001
lwb build --results results/experiment-001 --output dist-experiment
```

To append new, distinctly named models to the bundled leaderboard, pass `--base results/2026-10-10` to `export-run`. Smoke runs and pending runs cannot be published as complete leaderboard entries. Export uses an allowlist of final-answer fields; inspect answer content and model aliases before publishing your own data. Raw run directories are git-ignored and are never deployed to Pages.

[Runner behavior and selective truncation replay →](docs/RUNNING.md)

## Reading the results

The main metric weights each grid center by `cos(latitude)`:

```text
accuracy = sum(weight of correct answers) / sum(weight of scored coordinates)
```

- **Invalid**: a completed response without an unambiguous extractable answer, or an output-truncated response. Included in the denominator and scored wrong.
- **Unresolved**: an API failure without a usable answer. Excluded from that model's numerator and denominator, with the excluded count retained.
- **Pending**: no terminal result yet. No final leaderboard score is published.

In the bundled snapshot, 3 Kimi requests are unresolved after recovery attempts; 320 answers across the models remain invalid. Land/water recall columns are conditional on valid labels and should be read alongside the primary metric.

The geographic reference labels lakes and floating ice shelves **Water**, and land, islands in lakes, and grounded ice **Land**. GSHHG and Natural Earth disagree on 54 coordinates; those coordinates remain scored and are marked in the inspector. The static reference is not absolute ground truth.

Model names are **experiment aliases reported by the configured gateways**, not independently verified provider identities or claims that these names are publicly available. The included runs used different reasoning settings and output caps, with selective replay of output-cap truncations and recovery of some failed requests. They are not matched-compute or repeated-sampling comparisons. See [result provenance](results/2026-10-10/provenance.json) and the [protocol](docs/PROTOCOL.md).

## Repository layout

```text
src/land_water_bench/    Runner, extraction, scoring, validation, CLI
  data/                 Frozen reference, policy, source checksums
configs/                Environment-based example connections
results/2026-10-10/      Public frozen results, provenance, checksums
web/                    HTML, CSS, JavaScript, and licensed local font
scripts/check_browser.py  Full interactive and responsive browser checks
tests/                  Offline unit and mock-API integration tests
docs/                   Protocol, running, reference, and Chinese guide
.github/workflows/      Checks and GitHub Pages deployment
```

## Development and verification

```bash
python -m pip install -e '.[dev,reference]'
python -m pytest -q
lwb verify
python -m build
python -m playwright install chromium
lwb build --output dist-check
lwb serve --directory dist-check --port 8733
# In another terminal:
python scripts/check_browser.py --url http://localhost:8733/ --output runs/browser-check
```

Tests use mock API transports: they do not consume model credits. The browser check covers sorting, filters, answer dialogs, downloadable scores, all map pixels in three modes, animation controls, and mobile layouts. [Reference rebuilding](docs/REFERENCE.md) is optional and requires the larger geographic downloads.

## Publishing

The [Pages workflow](.github/workflows/pages.yml) verifies the saved results, builds `dist/`, and deploys it with GitHub's official Pages actions. It does not deploy the repository root or use API credentials. In a fork, enable **Settings → Pages → GitHub Actions** and update the repository links in `web/index.html` and this README. Runtime asset URLs are relative and work under a repository subpath.

## License and attribution

Original code and documentation: [MIT](LICENSE). GSHHG-derived reference labels and their copies in result/site files retain [LGPL-3.0](licenses/LGPL-3.0.txt) notices; the MIT license does not relicense upstream data. Natural Earth comparison data are public domain. Space Grotesk is distributed under the SIL Open Font License. Saved model answer text is provided as experimental evidence; no claim is made to override any applicable third-party terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

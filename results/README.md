# Frozen result releases

The default release is **2026-10-11**: 12 models, two tasks, 16,200 coordinates per model/task, 388,800 records. `lwb verify` checks both tasks; `lwb build` produces the unified terrain-first leaderboard.

- `2026-10-11/release.json`: release layout and source data hashes.
- `2026-10-11/terrain/`: joint Solid/Water and elevation results, final answer bodies, predictions, completion metadata and masked reference.
- `2026-10-11/classification/`: independent classification results, final answer bodies, settings and replay/recovery provenance.
- `2026-10-11/RESULTS.md`: combined scores and scoring rules.
- `2026-10-10/`: unchanged historical classification snapshot; explicitly select it with `lwb verify --results results/2026-10-10`.

Every result package has a `FILES.sha256` manifest. The complete terrain input is bundled under `src/land_water_bench/data/terrain-global-dataset.json`; its SHA-256 remains the exact pre-run reference hash. The latest result texts and metrics are byte-identical to the audited 2026-10-11 answer-revision export. Publication removes an absolute local recovery path from classification provenance and exposes only a run identifier.

The latest extraction revision restored 41 terrain answers and 280 classification answers, and reclassified four filtered terrain responses as unresolved. Invalid totals are now 471 / 40; unresolved totals are 723 / 2. The former scores and original run evidence were preserved, and re-extraction made zero model calls.

`response-metadata.json` retains the completion reasons, output counts, source-response hashes and line references needed to distinguish filtered and truncated responses. Full provider envelopes, private endpoints, request headers and operational logs are not published. The included evidence is sufficient to reproduce all displayed scores and answer extraction, but not independently attest provider identity or reproduce a historical model response.

Do not edit a frozen collection. Create and seal a new directory for new runs or scoring rules. Assets live in `web/` and are assembled from these collections rather than being duplicated in each result package. Derived references retain their upstream notices; see [third-party notices](../THIRD_PARTY_NOTICES.md).

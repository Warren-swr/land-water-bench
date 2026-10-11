# Terrain / Elevation Pilot

Experimental extension: input latitude and longitude; predict `{"type":"Solid|Water","elevation_m":number}`. For water, elevation refers to the bed, not the water surface. This is a separate pilot protocol and leaderboard; the original land/water benchmark and frozen results remain unchanged.

## Dataset

The default pilot has 512 coordinates: 64 in each of eight reference-height strata. Solid strata are <200, 200–1,000, 1,000–2,500, and ≥2,500 m. Water strata use depths of <500, 500–2,500, 2,500–4,500, and ≥4,500 m. Each question has equal weight. This balanced sample does not estimate global area accuracy.

Coordinates come from the original integer-centered 2° grid. Scope: −59° to +59°, ordinary GSHHG land or ocean, no known GSHHG/Natural Earth classification disagreements or exact boundary ties, and absolute reference height at least 25 m. Lakes, polar ice and nested islands are outside this pilot.

Classification uses the frozen GSHHG 2.3.7 reference. Elevation uses NOAA ETOPO 2022 15 arc-second ice-surface data, referenced to EGM2008, via the NOAA OceanWatch ERDDAP mirror. Each coordinate lies at the midpoint of four native cells; the reference is their bilinear average. The 60 arc-second product supplies a small candidate grid; candidates are re-stratified with final 15 arc-second heights before model calls. Selection is deterministic and never uses model answers.

Source CSVs, coordinate-registration checks, interpolation inputs, version metadata and hashes are retained. This is a grid-defined reference, not error-free physical ground truth. The 100 m tolerance measures disagreement with that grid. The spread of neighboring cells describes local terrain variation, not survey uncertainty.

## Commands

Use the same environment-based connection format as the main runner. Private workspace adapters are not required for outside users.

```bash
# Download a small candidate grid and native cells; freeze a new dataset.
python -m land_water_bench.terrain_reference \
  --sources runs/terrain-reference-001 \
  --classification-details results/2026-10-10/reference.details.json \
  --output runs/terrain-reference-001/dataset.json

# Independent, direct requests; 512 concurrent requests PER MODEL.
python -m land_water_bench.terrain \
  --config configs/local.yaml --models responses-model messages-model \
  --dataset runs/terrain-reference-001/dataset.json \
  --run-dir runs/terrain-pilot-001 --concurrency 512 \
  --max-retries 1000 --max-timeout-retries 2 --timeout 90

# Recompute and independently verify scores, then build a local comparison page.
python -m land_water_bench.terrain_results \
  --run-dir runs/terrain-pilot-001 --output dist-terrain-001 --seal
lwb serve --directory dist-terrain-001 --port 8734
```

Run the same command to resume an unsealed run. Endpoint errors keep their unknown state. Rate limits share the bounded 1,000-retry policy; read timeouts have a separate two-retry limit. The timeout doubles after the first read timeout. No valid or malformed completed answer is retried to improve its score.

## Scoring

Primary: joint accuracy at ±500 m, defined before inference. A success requires both the correct type and an elevation within tolerance. Also report ±100 m and ±1,000 m, type accuracy, MAE, median absolute error, P90, coverage and per-stratum metrics.

- Invalid completed answers count wrong and stay in the accuracy denominator.
- Unresolved API requests are excluded from numerator and denominator with their counts retained, following the existing experiment policy.
- Pending requests prevent final accuracy publication.
- Numeric error summaries cover valid numeric answers, including wrong-type answers; read them alongside numeric coverage.

The parser accepts one answer JSON in final-answer text, including a fenced object or explanatory prose. It rejects multiple answers, duplicate keys, nonfinite or nonnumeric elevations, ranges, missing/extra fields and truncated responses. Provider thinking/reasoning blocks are never treated as answers.

The shared runner still records exact prompts, requests, raw responses, attempts and source snapshots. The exporter independently rechecks all answer extraction and joint scores; its public projection omits endpoint addresses, API keys, request payloads and provider reasoning blocks.

## Interpretation

These are model aliases from configured gateways, not independent attestations of provider identity. Reasoning settings are forwarded as configured and may differ by protocol. Single-sample pilot differences do not establish a stable ranking. The visual terrain consists only of measured sample points, with vertical exaggeration; it does not interpolate an unmeasured world surface.

# Classification-only evaluation protocol

The primary task now also predicts terrain elevation; see [the global terrain protocol](TERRAIN-GLOBAL.md). This page documents the independent classification task and its historical experiments.

## Input and sampling

Protocol: `land-water/1.0-center-2deg`. Sample a 180 × 90 grid at cell centers: latitudes 89, 87, …, −89; longitudes −179, −177, …, 179. IDs are `row * 180 + column`, ordered north to south, west to east. The exact prompt is one line:

```text
Latitude: {lat:.1f}, Longitude: {lon:.1f}. Land or Water? Reply with exactly one word: Land or Water.
```

Each coordinate gets an independent user message. No system prompt, agent tools, web search, reference labels, images, or previous messages are supplied. Temperature is left at the endpoint default. The new standalone runner shuffles with seed `20261009` and has a distinct queue and concurrency limit for each model.

## Answer extraction

Current version: `land-water-answer/2.2-explicit-labels`; the historical 2026-10-10 snapshot retains `land-water-answer/2.1-boundary-lines`. Only the returned **answer text** is considered. Provider reasoning/thinking blocks are excluded.

An exact `Land` or `Water` is accepted case-insensitively. A final explicit answer line may have balanced Markdown wrappers, a code fence, simple punctuation, or an explicit answer prefix. A leading standalone answer can be followed by an explanation mentioning either geographic category. Explicit conflicting answers and uncertainty about the classification remain invalid; incidental mentions of land/water or possible seasonal sea ice do not override the explicit answer. The old 2.1 parser used a broader contrary-word check; its saved snapshot remains verifiable with that version. Conflicting consecutive final labels, ambiguous prose, unclosed fences, empty text, and output truncation remain invalid. The parser does not inspect the reference or model identity.

Every bundled response is checked against this parser. The public provenance lists incomplete responses separately because the displayed text alone cannot establish whether the provider stopped at its output cap.

## Score and exclusions

Policy: `land-water-score/2.0-exclude-unresolved`. A point's weight is `cos(latitude in radians)`. This is proportional to the area of an equal-angle cell at these fixed latitude centers.

The numerator sums weights of correct, valid labels. The denominator includes valid and invalid terminal answers. API failures without an answer are unresolved and excluded **per model**. Counts and area coverage remain visible in the data. Pending runs do not have a final primary score. If all points are unresolved, no final score is defined and the run cannot enter the leaderboard.

Land and water recall use valid predictions only. They can therefore look better than the main score for a model with many invalid answers. Point accuracy is unweighted and is not the ranking metric. Sorting is descending primary weighted accuracy, then model alias for a tie.

## Included experiments

The current 2026-10-11 classification release has 40 invalid answers and 2 unresolved requests across 194,400 records. It includes the subsequent recovery and extraction corrections. [Current provenance](../results/2026-10-11/classification/provenance.json).

The 2026-10-10 release contains 12 experiment aliases and 194,400 coordinate records. The 3 unresolved Kimi requests at (27° N, 91° E), (27° N, 93° E), and (27° N, 95° E) are gateway content-filter failures. They remain unresolved after recovery attempts and are excluded only for Kimi. There are 320 invalid answers; they count as wrong.

Responses-style models requested reasoning effort `low`. Messages-style models requested thinking with a 1,024-token budget. Initial runs used a 4,096-token output cap; later runs used 65,536. Kimi, DeepSeek, Step and MiMo contain selective replacements for output-cap truncations. Sonnet, Haiku, GPT-5.6 Sol and GPT-5.6 Terra used 65,536 in their full runs. Other completed malformed answers were not resampled to improve their score. Saved gateway/timeouts were retried; the collection keeps the final chosen response for each coordinate. See each model's settings and replay IDs in [provenance.json](../results/2026-10-10/provenance.json).

Concurrency differed historically: the initial Seed/Astra run shared a 512-request pool, later five-model runs used 256 per model, and the GPT-5.6 pair used 512 per model. These details are historical provenance; the standalone runner now consistently interprets `--concurrency` **per model**.

## Geographic reference and limits

Policy: `land-water-reference/2.0-gshhg-grounding`. GSHHG 2.3.7 full-resolution geometry labels oceans, lakes, ponds inside islands, and floating ice shelves as Water; land, islands in lakes, and grounded ice as Land. Nested polygon depth wins. Antarctic L6 grounding lines are primary; L5 ice-front lines are retained for sensitivity analysis.

GSHHG polygon tests were cross-checked with an independently implemented even/odd ray classifier and 14 named anchor points. Natural Earth 1:10m land/lakes disagree with GSHHG at 54 sampled coordinates. Those points are kept in scoring and marked, rather than selecting a reference based on the models' answers. The ice-front policy changes 159 grid labels.

This is a frozen geographic definition, not a guarantee about the present physical world. Coastlines, narrow rivers, seasonal water, source survey quality, exact boundaries, and ice definitions remain limitations. A 2° grid also misses many small geographic features.

## Interpretation

These are single-sample experiments with unequal reasoning budgets, output caps, and some recorded recovery/replay. Small score differences are not evidence of statistically stable model superiority. Model names are endpoint experiment aliases, not independently attested model identities or a catalog of publicly available products. The published responses and scores can be audited offline; reproducing an API response or accessing the same backend is not guaranteed.

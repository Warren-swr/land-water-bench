# Frozen result collections

`2026-10-10/` contains 12 public experiment aliases and 194,400 final coordinate responses. All scores and answer bodies were preserved from the local collection; this release reorganizes and verifies them without making new model requests.

- `data.json`: ranking metrics, prediction strings, reference metadata, and display names.
- `<model>.details.json`: every coordinate's ID, status, label, and final answer text.
- `reference.details.json`: reference category, lake names, and uncertainty flags.
- `provenance.json`: parser/scoring versions, historical settings, incomplete-response IDs, selective replay metadata, and unresolved IDs. Model aliases are not independent identity attestations.
- `verification.json`: offline verification receipt; recompute it with `lwb verify`.
- `FILES.sha256`: immutable content checksums; Git records the release's history.

Private endpoint configuration, credentials, request headers, provider response envelopes, and operational logs are excluded. This is enough to reproduce all displayed scores and audit final-answer extraction, but not to independently attest the original gateway/model identity. The historical raw evidence remains with the experiment owner.

Do not edit a frozen collection. Export a new directory for new runs or scoring policies. API availability, provider behavior, geographic reference versions, and parsing versions must be reported with a new submission. See the [protocol](../docs/PROTOCOL.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).

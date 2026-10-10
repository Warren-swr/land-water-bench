# Contributing

Open an issue for reference-policy or sampling changes before comparing new results with an existing release. Keep geographic reference construction independent of model answers.

Install `.[dev,reference]`, run `python -m pytest -q` and `lwb verify`, and run the browser check when changing rendering or interaction. No paid API calls are required for tests or site builds.

For a new result, use a distinct model alias and run ID, preserve the exact prompt, record API protocol/settings and any replay policy, and include every grid coordinate. Use `verify-run --seal` followed by `export-run`; never edit a frozen result or select only correct outputs. Explain any unresolved requests, output-cap replays, or altered budgets. API aliases are not proof of underlying model identity.

Keep credentials, gateway connection URLs, full provider envelopes, and local run directories out of commits. The allowlisted exporter intentionally publishes only final answer text and scoring metadata; review that text and metadata before publishing. Use environment-variable references in example configuration. Do not add paid evaluations to CI.

Reference/data changes require a new result version and explicit score verification. Derived data retain their upstream license notices. Preserve the font's OFL license and notices in the static site.

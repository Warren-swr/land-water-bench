# Geographic reference

The packaged `src/land_water_bench/data/dataset.json` is the frozen 16,200-point GSHHG reference used for the included scores. The runner uses this file without downloading geometry. `protocol.json` records the land/water policy. `reference.details.json` contains compact inspector metadata.

The full-resolution GSHHG 2.3.7 shapefile and native-binary archives, and Natural Earth land/lake GeoJSON at a pinned commit, are listed with exact SHA-256 checksums in `sources.json`. Source archives are not stored in Git.

## Rebuild from pinned public sources

```bash
python -m pip install -e '.[reference]'
lwb download-reference --output runs/reference-sources
lwb build-reference --sources runs/reference-sources --output runs/reference-rebuilt
```

The download is approximately 285 MB before extraction. Existing files are reused only if their hashes match. A changed upstream file causes a checksum failure, rather than silently changing the reference. The builder compares against the packaged grid but derives labels only from geographic polygons; no model responses are read.

To verify exact reproducibility of the dataset file:

```bash
python - <<'PY'
from importlib.resources import files
from pathlib import Path
expected = files('land_water_bench').joinpath('data/dataset.json').read_bytes()
actual = Path('runs/reference-rebuilt/dataset.json').read_bytes()
assert actual == expected
print('Reference dataset matches byte for byte')
PY
```

The output also contains per-feature diagnostics, named anchors, comparison disagreements, geometry-repair evidence, and dependency versions. Timestamps and diagnostic comparisons against the input baseline can differ; the reference dataset should not.

## Construction details

GSHHG L1–L4 polygons represent land, lakes, islands in lakes, and ponds in islands. Antarctic L6 native binary rings establish the grounding line, with L5 ice-front rings as a sensitivity check. Antarctic rings are projected into a south-polar azimuthal-equidistant plane, avoiding the split shapefile's dateline closure issue. Two invalid Antarctic rings are repaired with GEOS linework, with zero sampled-label changes and independent ray-casting agreement required. Unexpected invalid non-Antarctic geometries fail the build.

The independent ray classifier is compared against GEOS coverage for candidate sampled points. Fourteen named anchors cover lakes, islands, cities, dateline oceans, and both poles. The Natural Earth comparison uses a fixed commit and is diagnostic only. Reference disagreements are not removed from scoring.

The source is static and based partly on older surveys. It does not establish exact contemporary water boundaries. See [the protocol](PROTOCOL.md) for policy and uncertainty, and [third-party notices](../THIRD_PARTY_NOTICES.md) for the upstream data license and original source attribution.

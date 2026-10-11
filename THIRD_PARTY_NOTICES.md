# Third-party notices

## GSHHG 2.3.7

Reference labels, categories, geometry-derived flags, and their copies in `src/land_water_bench/data/`, `results/`, and generated sites are derived from GSHHG 2.3.7 by Paul Wessel and Walter H. F. Smith. Changes made by this project: sampled a fixed 2° center grid, applied the nested land/lake policy and Antarctic grounding line, and generated binary labels and per-point diagnostics. The source polygons have not been bundled or represented as newly authored data.

- [Official source and licensing notice](https://www.soest.hawaii.edu/pwessel/gshhg/)
- [Upstream README](licenses/GSHHG-README.txt)
- [GNU LGPL version 3](licenses/LGPL-3.0.txt), incorporating [GNU GPL version 3](licenses/GPL-3.0.txt)
- Exact source URLs and hashes: `src/land_water_bench/data/sources.json`.
- Corresponding derivation code: `src/land_water_bench/reference_builder.py`; rebuild instructions: [docs/REFERENCE.md](docs/REFERENCE.md).

Retain these notices when redistributing derived labels. Code MIT licensing does not override the geographic data license. The complete pinned upstream archives remain available through the listed official download URLs.

Suggested source citation: Wessel, P., and W. H. F. Smith (1996), A Global Self-consistent, Hierarchical, High-resolution Shoreline Database, Journal of Geophysical Research, 101, 8741–8743.

## Natural Earth

Comparison labels and lake names use Natural Earth 1:10m land/lakes at commit `ca96624a56bd078437bca8184e78163e5039ad19`. The geographic data are [public domain](https://www.naturalearthdata.com/about/terms-of-use/). Made with Natural Earth. These data are diagnostic; they do not replace the primary reference based on agreement with model answers.

## Space Grotesk

`web/display.ttf` is Space Grotesk, Copyright 2020 The Space Grotesk Project Authors. See the unmodified [SIL Open Font License](web/FONT-LICENSE.txt) bundled beside the font and copied to every site build.

## three.js

`web/unified/vendor/three.module.min.js` is a minified subset of [three.js](https://threejs.org/) r186 (npm `three@0.186.1`), Copyright 2010-2026 three.js authors, under the [MIT License](web/unified/vendor/THREE-LICENSE.txt). It is copied unchanged into every unified site build. Rebuild steps are in `web/unified/vendor/README.txt`.

## Model responses and inspiration

The coordinate responses in `results/` are saved outputs from the named experiment aliases. Original compilation, scoring code, documentation, and presentation are MIT-licensed where owned by the contributors. Model responses and trademarks are not represented as project-authored intellectual property or relicensed to override third-party terms. No model weights, service credentials, or access rights are included.

The experiment was inspired by [Celeste's public post](https://x.com/celestepoasts/status/2103232383139057950). This repository is an independent reproduction, not an official implementation or endorsement by the author or model providers. No imagery or text from the post is bundled.

## NOAA ETOPO 2022 (global terrain task and pilot)

The global terrain task and optional pilot use NOAA NCEI ETOPO 2022 terrain and bathymetry grids, accessed through the NOAA OceanWatch ERDDAP mirror. Heights are referenced to EGM2008. Source data may be used and redistributed freely; NOAA supplies them without warranties of accuracy or fitness. The bundled `src/land_water_bench/data/terrain-global-dataset.json` includes the four native cell values per coordinate, interpolation results, reference masks, and source metadata. The original source files, access URLs, and hashes are retained with each reference run; source CSV slices can be downloaded with the reference builder.

- Product: https://www.ncei.noaa.gov/products/etopo-global-relief-model
- DOI: https://doi.org/10.25921/fd45-gt74
- Mirror and data-use statement: https://oceanwatch.pifsc.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s.html

GSHHG-derived classification labels and the terrain page's background grid retain the GSHHG license notices above.

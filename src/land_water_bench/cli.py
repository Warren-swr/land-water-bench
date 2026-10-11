"""Command-line entry points. Building and verifying results never call a model."""

import argparse
import json
from importlib.resources import files
from pathlib import Path
import sys


def main():
    commands = {"run", "run-terrain", "verify", "verify-run", "export-run", "export-terrain-run", "build", "serve", "download-reference", "build-reference"}
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        from .runner import main as run
        return run(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        from .serve import main as serve
        return serve(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "run-terrain":
        from .terrain_global import main as run_terrain
        return run_terrain(sys.argv[2:])
    from .release import DEFAULT_RESULTS
    parser = argparse.ArgumentParser(description="Land / Water / Elevation Bench: direct API evaluation and an offline leaderboard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Call model endpoints (run --help for options)")
    sub.add_parser("run-terrain", help="Predict type and elevation on the bundled global grid (run-terrain --help)")
    sub.add_parser("serve", help="Serve a built site locally (serve --help for options)")
    verify = sub.add_parser("verify", help="Verify hashes, answer extraction, and every saved score")
    verify.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    run = sub.add_parser("verify-run", help="Audit a completed raw run; optionally freeze it")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--seal", action="store_true")
    export = sub.add_parser("export-run", help="Create a public result collection from a full-grid run")
    export.add_argument("--run-dir", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--base", type=Path, help="Append to an existing collection without modifying it")
    terrain_export = sub.add_parser("export-terrain-run", help="Verify a completed single-model terrain run and build its page")
    terrain_export.add_argument("--run-dir", type=Path, required=True)
    terrain_export.add_argument("--output", type=Path, required=True)
    terrain_export.add_argument("--assets", type=Path, default=Path("web/terrain-global"))
    build = sub.add_parser("build", help="Build a static leaderboard from verified saved results")
    build.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    build.add_argument("--output", type=Path, default=Path("dist"))
    build.add_argument("--assets", type=Path, help="Default: web/unified for two-task releases, web for classification")
    download = sub.add_parser("download-reference", help="Download checksum-pinned geographic sources (~285 MB)")
    download.add_argument("--output", type=Path, required=True)
    reference = sub.add_parser("build-reference", help="Rebuild the geographic reference; needs the reference extra")
    reference.add_argument("--sources", type=Path, required=True)
    reference.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from . import results
    if args.command == "verify":
        if (args.results / "release.json").exists():
            from .release import verify_release
            result = verify_release(args.results)
        else:
            result = results.verify_results(args.results)
    elif args.command == "verify-run":
        result = results.verify_run(args.run_dir, write=args.seal)
    elif args.command == "export-run":
        result = results.export_run(args.run_dir, args.output, args.base)
    elif args.command == "export-terrain-run":
        from .terrain_global_collection import export_run
        result = export_run(args.run_dir, args.output, args.assets)
    elif args.command == "build":
        if (args.results / "release.json").exists():
            from .release import build_release
            result = build_release(args.results, args.output, args.assets or Path("web/unified"))
        else:
            result = results.build_site(args.results, args.output, args.assets or Path("web"))
    elif args.command == "download-reference":
        from .reference_sources import download
        result = download(args.output)
    elif args.command == "build-reference":
        from .reference_builder import build
        build(args.sources, Path(str(files("land_water_bench").joinpath("data/dataset.json"))), args.output)
        result = {"passed": True, "output": str(args.output)}
    else:
        parser.error(f"Choose one of {', '.join(sorted(commands))}")
    print(json.dumps(result, ensure_ascii=False, indent=2))

"""Command-line entry points. Building and verifying results never call a model."""

import argparse
import json
from importlib.resources import files
from pathlib import Path
import sys


def main():
    commands = {"run", "verify", "verify-run", "export-run", "build", "serve", "download-reference", "build-reference"}
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        from .runner import main as run
        return run(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        from .serve import main as serve
        return serve(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Land / Water Bench: direct API evaluation and an offline leaderboard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Call model endpoints (run --help for options)")
    sub.add_parser("serve", help="Serve a built site locally (serve --help for options)")
    verify = sub.add_parser("verify", help="Verify hashes, answer extraction, and every saved score")
    verify.add_argument("--results", type=Path, default=Path("results/2026-10-10"))
    run = sub.add_parser("verify-run", help="Audit a completed raw run; optionally freeze it")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--seal", action="store_true")
    export = sub.add_parser("export-run", help="Create a public result collection from a full-grid run")
    export.add_argument("--run-dir", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--base", type=Path, help="Append to an existing collection without modifying it")
    build = sub.add_parser("build", help="Build a static leaderboard from verified saved results")
    build.add_argument("--results", type=Path, default=Path("results/2026-10-10"))
    build.add_argument("--output", type=Path, default=Path("dist"))
    build.add_argument("--assets", type=Path, default=Path("web"))
    download = sub.add_parser("download-reference", help="Download checksum-pinned geographic sources (~285 MB)")
    download.add_argument("--output", type=Path, required=True)
    reference = sub.add_parser("build-reference", help="Rebuild the geographic reference; needs the reference extra")
    reference.add_argument("--sources", type=Path, required=True)
    reference.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from . import results
    if args.command == "verify":
        result = results.verify_results(args.results)
    elif args.command == "verify-run":
        result = results.verify_run(args.run_dir, write=args.seal)
    elif args.command == "export-run":
        result = results.export_run(args.run_dir, args.output, args.base)
    elif args.command == "build":
        result = results.build_site(args.results, args.output, args.assets)
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

"""Fetch pinned public reference sources and extract only full-resolution coastlines."""

from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
import json
from pathlib import Path
import shutil
from urllib.request import Request, urlopen
import zipfile

from .core import digest


def download(output: Path):
    manifest_bytes = files("land_water_bench").joinpath("data/sources.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    output.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        target = output / item["file"]
        if not target.exists():
            partial = target.with_suffix(target.suffix + ".part")
            with urlopen(Request(item["url"], headers={"User-Agent": "LandWaterBench/1.0"}), timeout=90) as response, partial.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            if digest(partial.read_bytes()) != item["sha256"]:
                raise ValueError(f"Source checksum mismatch: {item['file']}")
            partial.replace(target)
        if digest(target.read_bytes()) != item["sha256"]:
            raise ValueError(f"Existing source checksum mismatch: {item['file']}")
        return item["file"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(fetch, manifest["files"]))
    for name in ("gshhg-shp-2.3.7.zip", "gshhg-bin-2.3.7.zip"):
        with zipfile.ZipFile(output / name) as archive:
            for member in archive.namelist():
                if not ((member.startswith("GSHHS_shp/f/") and not member.endswith("/")) or member == "gshhs_f.b"):
                    continue
                target = (output / member).resolve()
                if output.resolve() not in target.parents:
                    raise ValueError("Unsafe archive path")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
    (output / "manifest.json").write_bytes(manifest_bytes)
    return {"passed": True, "files": downloaded, "output": str(output)}

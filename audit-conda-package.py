#!/usr/bin/env python3
"""Summarize install-speed-relevant properties of a built conda package.

This script is intentionally standalone: run it after build-lsst-conda.sh finishes,
pointing at a built .conda or legacy .tar.bz2 package. It does not participate in
or modify the package build path.
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TarEntry:
    path: str
    size: int
    kind: str


def human_size(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def run_tar_list(archive: Path, compression_flag: str) -> list[TarEntry]:
    cmd = ["tar", compression_flag, "-tvf", str(archive)]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    entries: list[TarEntry] = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) < 6:
            continue
        mode, _owner, size_text, _date, _time, name = parts
        name = name.removeprefix("./")
        kind = "dir" if mode.startswith("d") else "symlink" if mode.startswith("l") else "file"
        try:
            size = int(size_text)
        except ValueError:
            size = 0
        entries.append(TarEntry(name, size, kind))
    return entries


def extract_info_file(archive: Path, compression_flag: str, member: str) -> str | None:
    for candidate in (member, f"./{member}"):
        cmd = ["tar", compression_flag, "-xOf", str(archive), candidate]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
    return None


def package_archives(package: Path, workdir: Path) -> tuple[Path, str, Path, str | None]:
    """Return (payload archive, tar compression flag, info archive, format name)."""
    if package.suffix == ".conda":
        with zipfile.ZipFile(package) as zf:
            names = zf.namelist()
            payload_names = [n for n in names if n.startswith("pkg-") and n.endswith(".tar.zst")]
            info_names = [n for n in names if n.startswith("info-") and n.endswith(".tar.zst")]
            if len(payload_names) != 1 or len(info_names) != 1:
                raise SystemExit(f"ERROR: expected one pkg-*.tar.zst and one info-*.tar.zst in {package}")
            payload = workdir / payload_names[0]
            info = workdir / info_names[0]
            zf.extract(payload_names[0], workdir)
            zf.extract(info_names[0], workdir)
        return payload, "--zstd", info, ".conda"

    if package.name.endswith(".tar.bz2"):
        return package, "-j", package, ".tar.bz2"

    raise SystemExit("ERROR: package must end in .conda or .tar.bz2")


def top_dirs(entries: list[TarEntry], depth: int, limit: int) -> list[tuple[str, int, int]]:
    totals: dict[str, int] = collections.defaultdict(int)
    counts: dict[str, int] = collections.defaultdict(int)
    for entry in entries:
        if entry.kind != "file":
            continue
        parts = Path(entry.path).parts
        key = "/".join(parts[:depth]) if len(parts) >= depth else entry.path
        totals[key] += entry.size
        counts[key] += 1
    ranked = sorted(totals, key=lambda key: (totals[key], counts[key]), reverse=True)
    return [(key, totals[key], counts[key]) for key in ranked[:limit]]


def print_table(rows: list[tuple[str, int, int]], title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    for path, size, count in rows:
        print(f"{human_size(size):>12}  {count:>8} files  {path}")


def summarize_info(info_archive: Path, compression_flag: str) -> None:
    has_prefix = extract_info_file(info_archive, compression_flag, "info/has_prefix")
    paths_json = extract_info_file(info_archive, compression_flag, "info/paths.json")

    print("\nRelocatability indicators")
    print("-------------------------")
    if has_prefix is None:
        print("info/has_prefix: absent")
    else:
        lines = [line for line in has_prefix.splitlines() if line.strip()]
        print(f"info/has_prefix: {len(lines)} entries")

    if paths_json is None:
        print("info/paths.json: absent")
        return

    paths = json.loads(paths_json).get("paths", [])
    prefix_paths = [p for p in paths if p.get("prefix_placeholder") or p.get("file_mode")]
    path_types = collections.Counter(p.get("path_type", "unknown") for p in paths)
    print(f"info/paths.json: {len(paths)} paths")
    print(f"paths with prefix/file-mode metadata: {len(prefix_paths)}")
    for path_type, count in sorted(path_types.items()):
        print(f"path_type {path_type}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a built .conda or .tar.bz2 package without modifying it.",
    )
    parser.add_argument("package", type=Path, help="Path to a built conda package")
    parser.add_argument("--top", type=int, default=20, help="Number of top directories to show")
    parser.add_argument("--depth", type=int, default=2, help="Directory depth for byte/file-count rollups")
    args = parser.parse_args()

    package = args.package.resolve()
    if not package.is_file():
        raise SystemExit(f"ERROR: package not found: {package}")
    if shutil.which("tar") is None:
        raise SystemExit("ERROR: GNU tar is required")

    with tempfile.TemporaryDirectory(prefix="conda-package-audit-") as tmp:
        payload_archive, payload_flag, info_archive, package_format = package_archives(package, Path(tmp))
        entries = [
            entry for entry in run_tar_list(payload_archive, payload_flag)
            if not entry.path.startswith("info/")
        ]

        files = [entry for entry in entries if entry.kind == "file"]
        dirs = [entry for entry in entries if entry.kind == "dir"]
        symlinks = [entry for entry in entries if entry.kind == "symlink"]
        total_size = sum(entry.size for entry in files)

        print("Conda package audit")
        print("===================")
        print(f"package: {package}")
        print(f"format: {package_format}")
        print(f"archive size: {human_size(package.stat().st_size)}")
        print(f"payload files: {len(files)}")
        print(f"payload directories: {len(dirs)}")
        print(f"payload symlinks: {len(symlinks)}")
        print(f"uncompressed payload bytes: {human_size(total_size)}")

        print_table(top_dirs(entries, args.depth, args.top), f"Top {args.top} directories by payload bytes")
        count_rows = sorted(top_dirs(entries, args.depth, len(entries)), key=lambda row: row[2], reverse=True)[:args.top]
        print_table(count_rows, f"Top {args.top} directories by file count")
        summarize_info(info_archive, payload_flag)

    return 0


if __name__ == "__main__":
    sys.exit(main())

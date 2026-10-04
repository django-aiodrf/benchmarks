"""A record of the host, the Python environment and the source of a run."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def source_record(directory: Path) -> dict:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*.py")):
        if any(part.startswith(".") for part in path.relative_to(directory).parts):
            continue
        digest.update(
            path.relative_to(directory).as_posix().encode()
            + b"\0"
            + path.read_bytes()
            + b"\0"
        )
    record = {
        "path": str(directory),
        "python_sha256": digest.hexdigest(),
        "git_head": None,
        "git_dirty": None,
    }
    try:
        commit = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "-C", str(directory), "status", "--porcelain", "--", "."],
            capture_output=True,
            text=True,
            check=False,
        )
        record.update(
            git_head=commit.stdout.strip() if commit.returncode == 0 else None,
            git_dirty=bool(dirty.stdout) if dirty.returncode == 0 else None,
        )
    except FileNotFoundError:
        pass  # Runtime images need source hashes, not a Git executable.
    return record


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _cpu_model() -> str | None:
    for line in (_read("/proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            return line.partition(":")[2].strip()
    return platform.processor() or None


def _memory_gib() -> float | None:
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            return round(int(line.split()[1]) / 1024**2, 1)
    return None


def snapshot() -> dict:
    sources = {"benchmark": source_record(Path(__file__).resolve().parent)}
    for package in ("aiodrf", "fastdrf"):
        spec = importlib.util.find_spec(package)
        if spec is not None and spec.origin:
            sources[package] = source_record(Path(spec.origin).parent)
    return {
        "packages": sorted(
            (
                {"name": item.metadata["Name"], "version": item.version}
                for item in importlib.metadata.distributions()
            ),
            key=lambda item: item["name"].lower(),
        ),
        "sources": sources,
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "cpu_model": _cpu_model(),
        "cpu_governor": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "memory_gib": _memory_gib(),
        "cpu_affinity": sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else None,
        "gil_enabled": sys._is_gil_enabled()
        if hasattr(sys, "_is_gil_enabled")
        else True,
    }


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(snapshot(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

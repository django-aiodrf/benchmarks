"""CPU used by each benchmark service container while a sample is measured.

The services run on their own CPUs (``compose.yaml``). The CPU time a
container used during a sample, divided by the sample's duration and by the
number of CPUs the container may use, tells whether a service limited the
throughput: a busy service is near 100%.
"""

import json
import subprocess
import time
from pathlib import Path

PROJECT = "aiodrf-benchmarks"
CGROUP_ROOT = Path("/sys/fs/cgroup")


def cpu_count(cpuset: str) -> int:
    """The number of CPUs in a cpuset such as ``16-19`` or ``26,27``."""
    total = 0
    for part in filter(None, cpuset.split(",")):
        first, _, last = part.partition("-")
        total += int(last or first) - int(first) + 1
    return total


def containers(project: str = PROJECT) -> dict[str, dict]:
    """Each running service of the Compose project: its cgroup and CPU count."""
    try:
        identifiers = subprocess.run(
            ["docker", "compose", "-p", project, "ps", "-q"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.split()
        if not identifiers:
            return {}
        details = json.loads(
            subprocess.check_output(
                ["docker", "inspect", *identifiers], text=True, timeout=10
            )
        )
    except OSError, subprocess.SubprocessError, ValueError:
        return {}
    result = {}
    for item in details:
        service = item["Config"]["Labels"].get("com.docker.compose.service")
        stat = CGROUP_ROOT / "system.slice" / f"docker-{item['Id']}.scope" / "cpu.stat"
        cpuset = item["HostConfig"].get("CpusetCpus") or ""
        if service and stat.is_file() and cpuset:
            result[service] = {"stat": stat, "cpus": cpu_count(cpuset)}
    return result


def _usage_usec(stat: Path) -> int:
    for line in stat.read_text().splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            return int(value)
    raise ValueError(f"No usage_usec in {stat}")


class ServiceUsage:
    """Measure the services' CPU use over a ``with`` block."""

    def __init__(self, services: dict[str, dict]):
        self.services = services
        self.result: dict[str, dict] = {}

    def __enter__(self):
        self.started = time.monotonic()
        self.before = {
            name: _usage_usec(s["stat"]) for name, s in self.services.items()
        }
        return self

    def __exit__(self, *exc):
        elapsed = time.monotonic() - self.started
        for name, service in self.services.items():
            try:
                used = _usage_usec(service["stat"]) - self.before[name]
            except OSError, ValueError:
                continue
            cores = used / 1e6 / elapsed if elapsed > 0 else 0.0
            self.result[name] = {
                "cores": round(cores, 3),
                "cpus": service["cpus"],
                "utilization": round(cores / service["cpus"], 3),
            }
        return False

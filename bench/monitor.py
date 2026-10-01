"""CPU and memory of the server's process tree during a sample."""

import statistics
import threading
import time

import psutil


class ResourceMonitor:
    """CPU uses one logical core as 100%; RSS sums shared pages across processes."""

    def __init__(self, pid: int, interval: float = 0.2):
        self.pid = pid
        self.interval = interval
        self.samples = []
        self.errors = []
        self._previous = {}
        self._last = time.monotonic()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="bench-monitor", daemon=True
        )

    def _sample(self):
        now = time.monotonic()
        try:
            root = psutil.Process(self.pid)
            processes = [root, *root.children(recursive=True)]
            memory, cpu = 0, 0.0
            current = {}
            for process in processes:
                with process.oneshot():
                    key = (process.pid, process.create_time())
                    times = process.cpu_times()
                    value = times.user + times.system
                    current[key] = value
                    cpu += max(0, value - self._previous.get(key, value))
                    memory += process.memory_info().rss
            if self._previous:
                self.samples.append(
                    {
                        "elapsed": now - self._start,
                        "cpu_percent": cpu / (now - self._last) * 100,
                        "rss_mib": memory / 1024**2,
                    }
                )
            self._previous, self._last = current, now
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            self.errors.append(type(exc).__name__)

    def _run(self):
        while not self._stopped.wait(self.interval):
            self._sample()

    def __enter__(self):
        self._start = self._last = time.monotonic()
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stopped.set()
        self._thread.join(timeout=5)
        self._sample()

    def summary(self) -> dict:
        if not self.samples or self.errors:
            return {
                "resources_available": False,
                "resource_errors": sorted(set(self.errors)),
            }
        return {
            "resources_available": True,
            "cpu_mean_percent": statistics.mean(
                row["cpu_percent"] for row in self.samples
            ),
            "cpu_peak_percent": max(row["cpu_percent"] for row in self.samples),
            "rss_mean_mib": statistics.mean(row["rss_mib"] for row in self.samples),
            "rss_peak_mib": max(row["rss_mib"] for row in self.samples),
        }

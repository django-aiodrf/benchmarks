"""Command construction and rejection of misleading load results."""

import copy
import json

import pytest

from bench.cli import parser
from bench.processes import server_command
from bench.profiles import WRITE_SCENARIOS
from bench.results import normalize, write_report
from bench.runner import load_command, run


def raw_result():
    return {
        "summary": {"successRate": 1, "requestsPerSec": 100, "total": 1},
        "statusCodeDistribution": {"200": 100},
        "errorDistribution": {},
        "latencyPercentiles": {"p50": 0.002, "p95": 0.005, "p99": 0.009},
    }


def test_latency_units_and_post_count():
    assert normalize(raw_result(), status=200)["p95_ms"] == 5
    with pytest.raises(ValueError, match="write volume"):
        normalize(raw_result(), status=200, expected_requests=101)


@pytest.mark.parametrize(
    "key,value",
    [
        ("errorDistribution", {"timeout": 1}),
        ("statusCodeDistribution", {"500": 100}),
        ("statusCodeDistribution", {}),
        ("summary", {"successRate": 0.99}),
    ],
)
def test_errors_invalidate_the_sample(key, value):
    raw = copy.deepcopy(raw_result())
    raw[key] = value
    with pytest.raises(ValueError):
        normalize(raw, status=200)


def test_nonfinite_metrics_are_rejected():
    raw = raw_result()
    raw["summary"]["requestsPerSec"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        normalize(raw, status=200)


def test_unknown_profile_is_rejected():
    assert "runbolt" in server_command("bolt", "127.0.0.1", 8100, 1)
    assert "uvicorn" in server_command("aiodrf", "127.0.0.1", 8100, 1)
    with pytest.raises(ValueError):
        server_command("unknown", "127.0.0.1", 8100, 1)


@pytest.mark.parametrize("scenario", sorted(WRITE_SCENARIOS))
def test_writes_use_fixed_volume_and_reads_use_duration(scenario):
    args = parser().parse_args(["run", "--output", "results/test"])
    read = load_command("oha", args, "json")
    write = load_command("oha", args, scenario)
    assert "-z" in read and "-w" in read
    assert "-n" in write and "-z" not in write
    assert "POST" in write
    assert "-z" in load_command("oha", args, scenario, warmup=True)


def test_measurement_requires_explicit_reset_and_postgres(monkeypatch):
    args = parser().parse_args(["run", "--output", "results/test"])
    with pytest.raises(ValueError, match="reset-database"):
        run(args)
    args.reset_database = True
    monkeypatch.setenv("BENCH_DATABASE", "sqlite")
    with pytest.raises(ValueError, match="PostgreSQL"):
        run(args)


def test_failed_repeat_is_not_ranked(tmp_path):
    records = [
        {
            "scenario": "json",
            "profile": "django",
            "valid": True,
            **normalize(raw_result(), status=200),
        },
        {"scenario": "json", "profile": "django", "valid": False, "error": "timeout"},
    ]
    (tmp_path / "samples.json").write_text(json.dumps(records))
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "state": "failed",
                "parameters": {
                    "repeats": 2,
                    "scenarios": ["json"],
                    "frameworks": ["django"],
                },
            }
        )
    )
    write_report(tmp_path)
    text = (tmp_path / "REPORT.md").read_text()
    assert "| django | uvicorn | invalid |" in text
    assert "| timeout |" in text
    assert "| 100 |" not in text


def test_the_load_generators_cpu_use_is_measured(tmp_path):
    import sys

    from bench.runner import load

    command = [
        sys.executable,
        "-c",
        "import json; sum(range(2_000_000)); print(json.dumps({'ok': True}))",
    ]
    raw, cpu = load(command, tmp_path / "oha.json", timeout=30)
    assert raw == {"ok": True}
    assert cpu["cpu_seconds"] > 0
    assert 0 < cpu["cores"] <= 1.5


def test_a_failing_load_generator_is_an_error(tmp_path):
    import subprocess
    import sys

    from bench.runner import load

    with pytest.raises(subprocess.CalledProcessError):
        load(
            [sys.executable, "-c", "raise SystemExit(3)"],
            tmp_path / "oha.json",
            timeout=30,
        )


def test_sigterm_runs_cleanup_so_no_server_is_left_running(tmp_path):
    import signal
    import subprocess
    import sys
    import time

    marker = tmp_path / "stopped"
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import pathlib, time\n"
            "from bench.cli import stop_on_sigterm\n"
            "stop_on_sigterm()\n"
            "try:\n"
            "    print('ready', flush=True)\n"
            "    time.sleep(30)\n"
            "finally:\n"
            f"    pathlib.Path({str(marker)!r}).touch()\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        assert child.stdout.readline() == "ready\n"
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=10)
    time.sleep(0.1)
    assert marker.exists()
    assert child.returncode == 128 + signal.SIGTERM

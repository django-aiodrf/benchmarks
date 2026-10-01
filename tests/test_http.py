"""Opt-in native HTTP contracts across every framework and both process counts."""

import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from bench.processes import ROOT, server
from bench.profiles import CORE_SCENARIOS, PATHS, PROFILES, SCENARIOS
from bench.servers import server_matrix
from bench.verify import verify

pytestmark = [
    pytest.mark.http,
    pytest.mark.skipif(
        os.environ.get("BENCH_HTTP_TESTS") != "1",
        reason="set BENCH_HTTP_TESTS=1 to start real servers",
    ),
]


MATRIX = server_matrix(PROFILES, ["all"], [1, 4])


@pytest.mark.parametrize(
    ("profile", "server_name", "workers"),
    MATRIX,
    ids=[f"{profile}-{server}-w{workers}" for profile, server, workers in MATRIX],
)
def test_real_http_contract(profile, server_name, workers, tmp_path, monkeypatch):
    check_contract(profile, workers, server_name, tmp_path, monkeypatch)


def check_contract(profile, workers, server_name, tmp_path, monkeypatch):
    # 40 rows: the paginated case reads the second page of 20.
    monkeypatch.setenv("BENCH_DATASET_SIZE", "40")
    selection = os.environ.get("BENCH_TEST_SCENARIOS", ",".join(CORE_SCENARIOS))
    scenarios = list(SCENARIOS) if selection == "all" else selection.split(",")
    # A separate process avoids pytest-django's test-database remapping.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "bench.cli",
            "prepare",
            "--reset-database",
            "--scenarios",
            *scenarios,
        ],
        cwd=ROOT,
        check=True,
        timeout=120,
        capture_output=True,
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with server(
        profile,
        host="127.0.0.1",
        port=port,
        workers=workers,
        log=tmp_path / "server.log",
        server_name=server_name,
    ):
        assert (
            verify(f"http://127.0.0.1:{port}", dataset_size=40, scenarios=scenarios)[
                "read_contract"
            ]
            == "passed"
        )
        # Sequential startup checks do not expose worker/greenlet contention.
        # Compare concurrent reads with the already verified endpoint payload.
        with (
            httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False
            ) as client,
            ThreadPoolExecutor(max_workers=16) as executor,
        ):
            for scenario in ("json", "db", "cache-read", "http-single"):
                if scenario not in scenarios:
                    continue
                path = PATHS[scenario]
                expected = client.get(path).json()
                responses = executor.map(client.get, [path] * 32)
                for response in responses:
                    assert response.status_code == 200
                    assert response.json() == expected
    # A successful HTTP response alone does not prove loop-owned client cleanup.
    assert (
        "Cannot use AsyncMongoClient in different event loop"
        not in (tmp_path / "server.log").read_text()
    )

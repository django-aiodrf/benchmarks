"""Worker, runtime and service workload contracts for the expanded suite."""

from bench.cli import parser
from bench.processes import server_command
from bench.profiles import PROFILES, SCENARIOS, required_services


def test_bolt_is_the_only_native_server_profile():
    assert "bolt" in PROFILES
    for profile in PROFILES:
        command = server_command(profile, "127.0.0.1", 8100, 4)
        if profile == "bolt":
            assert "runbolt" in command
            assert command[command.index("--processes") + 1] == "4"
        elif profile in ("drf", "django-sync"):
            assert command[command.index("--interface") + 1] == "wsgi"
        else:
            assert "uvicorn" in command


def test_worker_matrix_and_execution_mode_are_explicit():
    args = parser().parse_args(["run", "--output", "results/test"])
    assert args.workers == [1, 4]
    assert args.mode == "baremetal"


def test_services_follow_selected_scenarios():
    assert required_services(["json"]) == set()
    assert required_services(["db"]) == {"postgres"}
    assert required_services(
        ["mongo-read", "elasticsearch-search", "mixed-thread"]
    ) == {"mongodb", "elasticsearch", "http"}
    assert {"http-single", "http-fanout", "mixed-inline", "mixed-thread"} <= set(
        SCENARIOS
    )


def test_cache_and_write_workloads_have_explicit_dependencies():
    assert required_services(["db-cache-hit", "articles-cache-miss"]) == {
        "postgres",
        "valkey",
    }
    assert required_services(["cache-read", "cache-write", "cache-pipeline"]) == {
        "valkey"
    }
    assert {"mongo-write", "elasticsearch-write", "http-no-reuse"} <= set(SCENARIOS)

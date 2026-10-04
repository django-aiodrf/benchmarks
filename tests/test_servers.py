"""Server settings and report groups keep transport comparisons explicit."""

import json

import pytest

from bench.cli import parser
from bench.processes import server_command
from bench.results import aggregate


@pytest.mark.parametrize("workers", [1, 4])
def test_granian_asgi_uses_uvloop_and_default_runtime_and_queues(workers):
    command = server_command("aiodrf-tuned", "127.0.0.1", 8100, workers, "granian-asgi")
    for name, value in {
        "--interface": "asgi",
        "--workers": str(workers),
        "--loop": "uvloop",
        "--http": "1",
    }.items():
        assert command[command.index(name) + 1] == value
    assert "bench.asgi:application" in command
    assert "--no-access-log" in command
    for flag in (
        "--runtime-mode",
        "--runtime-threads",
        "--runtime-blocking-threads",
        "--blocking-threads",
        "--backlog",
        "--backpressure",
        "--task-impl",
    ):
        assert flag not in command


@pytest.mark.parametrize("name", ["sync", "gthread", "gevent"])
def test_gunicorn_keeps_the_worker_class_explicit(name):
    command = server_command("drf", "127.0.0.1", 8100, 4, f"gunicorn-{name}")
    assert command[command.index("--worker-class") + 1] == name
    assert command[command.index("--threads") + 1] == (
        "8" if name == "gthread" else "1"
    )
    assert command[command.index("--keep-alive") + 1] == "5"
    assert "--preload" not in command
    assert "bench.wsgi:create_application()" in command


def test_granian_wsgi_has_a_bounded_explicit_thread_pool():
    command = server_command("django-sync", "127.0.0.1", 8100, 1, "granian-wsgi")
    assert command[command.index("--interface") + 1] == "wsgi"
    for name, value in {
        "--runtime-mode": "mt",
        "--runtime-threads": "1",
        "--blocking-threads": "4",
        "--backpressure": "128",
        "--backlog": "128",
    }.items():
        assert command[command.index(name) + 1] == value
    assert "--factory" in command


@pytest.mark.parametrize(
    "profile",
    [
        "aiodrf",
        "aiodrf-tuned",
        "adrf",
        "ninja",
        "django",
        "fastapi",
        "litestar",
        "bolt",
    ],
)
def test_wsgi_rejects_async_profiles_instead_of_changing_the_workload(profile):
    with pytest.raises(ValueError, match="runs on"):
        server_command(profile, "127.0.0.1", 8100, 1, "granian-wsgi")


@pytest.mark.parametrize("server", ["uvicorn", "granian-asgi"])
def test_drf_runs_on_wsgi_only(server):
    with pytest.raises(ValueError, match="runs on"):
        server_command("drf", "127.0.0.1", 8100, 1, server)


def test_plain_synchronous_django_runs_on_wsgi_and_asgi():
    from bench.servers import SERVERS, servers_for

    assert servers_for("django-sync") == (
        "granian-wsgi",
        "gunicorn-sync",
        "gunicorn-gthread",
        "gunicorn-gevent",
        "uvicorn",
        "granian-asgi",
    )
    assert "bench.asgi:application" in server_command(
        "django-sync", "127.0.0.1", 8100, 1, "uvicorn"
    )
    assert set(servers_for("django-sync")) < set(SERVERS)


def test_every_framework_runs_on_each_server_of_its_interface():
    from bench.profiles import INTERFACES, PROFILES
    from bench.servers import ASGI_SERVERS, WSGI_SERVERS, server_matrix

    matrix = server_matrix(PROFILES, ["all"], [1, 4])
    assert len(matrix) == len(set(matrix)) == 62
    for profile, server, _ in matrix:
        expected = {
            "asgi": ASGI_SERVERS,
            "wsgi": WSGI_SERVERS + (ASGI_SERVERS if profile == "django-sync" else ()),
            "native": ("bolt-native",),
        }[INTERFACES[profile]]
        assert server in expected
    for profile in ("aiodrf", "aiodrf-tuned"):
        assert {(s, w) for p, s, w in matrix if p == profile} == {
            (server, workers) for server in ASGI_SERVERS for workers in (1, 4)
        }
    for profile in ("drf", "django-sync"):
        assert {s for p, s, _ in matrix if p == profile} >= set(WSGI_SERVERS)


def test_cli_defaults_measure_every_server_of_each_framework():
    args = parser().parse_args(["run", "--output", "results/test"])
    assert args.servers == ["all"]
    assert "runbolt" in server_command("bolt", "127.0.0.1", 8100, 1)
    assert "uvicorn" in server_command("aiodrf", "127.0.0.1", 8100, 1)
    assert "granian" in server_command("drf", "127.0.0.1", 8100, 1)
    with pytest.raises(ValueError, match="runs on"):
        server_command("bolt", "127.0.0.1", 8100, 1, "granian-asgi")


def test_a_selection_without_a_frameworks_server_is_refused():
    from bench.servers import server_matrix

    with pytest.raises(ValueError, match="No selected server runs drf"):
        server_matrix(["aiodrf", "drf"], ["uvicorn"], [1])


def test_uvicorn_uses_uvloop_and_httptools():
    command = server_command("fastapi", "127.0.0.1", 8100, 1, "uvicorn")
    assert command[command.index("--loop") + 1] == "uvloop"
    assert command[command.index("--http") + 1] == "httptools"
    assert "bench.asgi:application" in command


def test_workers_are_pinned_to_one_cpu_each():
    from bench.processes import pinned

    command = ["python", "-m", "uvicorn"]
    assert pinned(command, None, 4) == command
    assert pinned(command, [0, 2, 4, 6], 1)[:3] == ["taskset", "-c", "0"]
    assert pinned(command, [0, 2, 4, 6], 4)[:3] == ["taskset", "-c", "0,2,4,6"]
    with pytest.raises(ValueError, match="4 processes need 4 CPUs"):
        pinned(command, [0, 2], 4)


def test_cpu_lists_accept_ranges_and_reject_duplicates():
    import argparse

    from bench.cli import cpu_list

    assert cpu_list("0,2,4,6") == [0, 2, 4, 6]
    assert cpu_list("8-11") == [8, 9, 10, 11]
    with pytest.raises(argparse.ArgumentTypeError):
        cpu_list("1,1")


def test_servers_are_never_averaged_in_reports(tmp_path):
    manifest = {
        "parameters": {
            "repeats": 1,
            "scenarios": ["json"],
            "frameworks": ["aiodrf"],
            "workers": [1],
            "servers": ["uvicorn", "granian-asgi"],
        },
    }
    records = [
        {
            "scenario": "json",
            "profile": "aiodrf",
            "workers": 1,
            "server": server,
            "valid": True,
            "rps": rps,
        }
        for server, rps in [("uvicorn", 100), ("granian-asgi", 200)]
    ]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "samples.json").write_text(json.dumps(records))
    _, rows = aggregate(tmp_path)
    assert {row["server"]: row["rps"] for row in rows} == {
        "uvicorn": 100,
        "granian-asgi": 200,
    }


def test_wsgi_resources_are_worker_owned_and_close_idempotently(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from bench import wsgi
    from bench.services import request_resources

    closed = []
    resource = object()

    @contextmanager
    def clients():
        try:
            yield resource
        finally:
            closed.append(True)

    monkeypatch.setattr(wsgi, "sync_resources", clients)

    def app(environ, start_response):
        assert request_resources(SimpleNamespace(META=environ)) is resource
        start_response("200 OK", [])
        return [b"ok"]

    application = wsgi.WorkerApplication(app)
    assert application({}, lambda *args: None) == [b"ok"]
    assert application({}, lambda *args: None) == [b"ok"]
    application.close()
    application.close()
    assert closed == [True]


def test_server_aliases_select_one_configuration():
    from bench.servers import server_matrix

    assert server_matrix(["aiodrf"], ["default", "uvicorn"], [1]) == [
        ("aiodrf", "uvicorn", 1)
    ]


@pytest.mark.parametrize(
    "message",
    [
        "Traceback (most recent call last):\nRuntimeError: response close failed",
        "RuntimeWarning: coroutine was never awaited",
        "[ERROR] Error handling request",
        "Unclosed client session",
    ],
)
def test_successful_http_does_not_hide_server_cleanup_errors(tmp_path, message):
    from bench.processes import check_server_log

    log = tmp_path / "server.log"
    log.write_text(message)
    with pytest.raises(RuntimeError, match="reported errors"):
        check_server_log(log)


def test_clean_server_log_is_accepted(tmp_path):
    from bench.processes import check_server_log

    log = tmp_path / "server.log"
    log.write_text("Server started\nServer shutdown\n")
    check_server_log(log)


def test_wsgi_workers_close_caches_synchronously(monkeypatch):
    monkeypatch.setenv("BENCH_SERVICE_CLIENTS", "1")
    # django-valkey swaps Django's request_finished receiver for an async one,
    # which runs async_to_sync after every WSGI request and fails under gevent.
    import django_valkey.base  # noqa: F401 -- what a worker's first cache use does
    from asgiref.sync import iscoroutinefunction
    from django.core import signals
    from django.core.cache import close_caches

    from bench import wsgi

    monkeypatch.setattr(
        wsgi, "sync_resources", lambda: __import__("contextlib").nullcontext()
    )
    monkeypatch.setattr("django.conf.settings.PROFILE", "django-sync")
    application = wsgi.create_application()
    try:
        receivers = [ref() for _, ref, *_ in signals.request_finished.receivers]
        assert close_caches in receivers
        assert not any(iscoroutinefunction(receiver) for receiver in receivers)
    finally:
        application.close()


def test_a_port_left_in_time_wait_counts_as_free():
    # gunicorn's sync worker closes every connection itself, which leaves the
    # server's port in TIME_WAIT for a minute after the server stops.
    import socket

    from bench.processes import ensure_port_free

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as client:
            accepted, _ = listener.accept()
            accepted.close()  # the server closes first
            client.recv(1)
    ensure_port_free("127.0.0.1", port)


def test_a_listening_port_is_refused():
    import socket

    from bench.processes import ensure_port_free

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(OSError):
            ensure_port_free("127.0.0.1", listener.getsockname()[1])

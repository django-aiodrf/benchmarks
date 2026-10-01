"""The servers each framework runs on, and their exact process settings."""

import sys
from itertools import product

from bench.profiles import INTERFACES, PROFILES

ASGI_SERVERS = ("uvicorn", "granian-asgi")
WSGI_SERVERS = ("granian-wsgi", "gunicorn-sync", "gunicorn-gthread", "gunicorn-gevent")
NATIVE_SERVERS = ("bolt-native",)
SERVERS = ASGI_SERVERS + WSGI_SERVERS + NATIVE_SERVERS
_BY_INTERFACE = {"asgi": ASGI_SERVERS, "wsgi": WSGI_SERVERS, "native": NATIVE_SERVERS}


def servers_for(profile: str) -> tuple[str, ...]:
    """The servers a framework is measured on, the first being its default."""
    if profile not in PROFILES:
        raise ValueError(f"Unknown framework: {profile}")
    if profile == "django-sync":
        # The same synchronous views under WSGI and under Django's ASGI handler.
        return WSGI_SERVERS + ASGI_SERVERS
    return _BY_INTERFACE[INTERFACES[profile]]


def resolve_server(profile: str, server: str = "default") -> str:
    supported = servers_for(profile)
    if server == "default":
        return supported[0]
    if server not in supported:
        raise ValueError(f"{profile} runs on {', '.join(supported)}, not {server}")
    return server


def server_matrix(profiles, servers, workers):
    """
    Every (framework, server, workers) combination to measure. ``servers`` is
    ``["all"]`` for each framework's servers, or names, of which each
    framework takes those it supports.
    """
    selected = None if list(servers) == ["all"] else set(servers)
    unknown = (selected or set()) - set(SERVERS) - {"default"}
    if unknown:
        raise ValueError(f"Unknown servers: {', '.join(sorted(unknown))}")
    combinations = []
    for profile in profiles:
        supported = servers_for(profile)
        if selected is None:
            chosen = supported
        else:
            chosen = tuple(
                server
                for server in supported
                if server in selected
                or ("default" in selected and server == supported[0])
            )
        if not chosen:
            raise ValueError(
                f"No selected server runs {profile}; it runs on {', '.join(supported)}"
            )
        combinations += [
            (profile, server, count) for server, count in product(chosen, workers)
        ]
    return combinations


def server_command(
    profile: str, host: str, port: int, workers: int, server: str = "default"
) -> list[str]:
    server = resolve_server(profile, server)
    if not 1 <= port <= 65535 or workers < 1:
        raise ValueError("Invalid port or worker count")
    if server == "bolt-native":
        return [
            sys.executable,
            "manage.py",
            "runbolt",
            "--host",
            host,
            "--port",
            str(port),
            "--processes",
            str(workers),
            "--no-admin",
        ]
    if server == "uvicorn":
        return [
            sys.executable,
            "-m",
            "uvicorn",
            "bench.asgi:application",
            "--host",
            host,
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--loop",
            "uvloop",
            "--http",
            "httptools",
            "--ws",
            "none",
            "--lifespan",
            "on",
            "--backlog",
            "2048",
            "--timeout-keep-alive",
            "5",
            "--no-access-log",
            "--log-level",
            "warning",
        ]
    if server.startswith("granian-"):
        interface = "wsgi" if server in WSGI_SERVERS else "asgi"
        command = [
            sys.executable,
            "-m",
            "granian",
            "--interface",
            interface,
            "--host",
            host,
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--runtime-mode",
            "mt" if workers > 1 else "st",
            "--runtime-threads",
            "1",
            # Granian's default number of runtime blocking threads:
            # "--runtime-blocking-threads",
            # "1",
            "--loop",
            "uvloop",
            "--http",
            "1",
            "--no-ws",
            "--backlog",
            "2048",
            "--backpressure",
            "256",
            "--http1-keep-alive",
            "--no-http1-pipeline-flush",
            "--no-access-log",
            "--log-level",
            "warning",
        ]
        if interface == "wsgi":
            return command + [
                "--blocking-threads",
                "8",
                "--factory",
                "bench.wsgi:create_application",
            ]
        return command + ["--task-impl", "asyncio", "bench.asgi:application"]
    worker = server.removeprefix("gunicorn-")
    return [
        sys.executable,
        "-m",
        "gunicorn",
        "bench.wsgi:create_application()",
        "--bind",
        f"{host}:{port}",
        "--workers",
        str(workers),
        "--worker-class",
        worker,
        "--threads",
        "8" if worker == "gthread" else "1",
        "--worker-connections",
        "256",
        "--backlog",
        "2048",
        "--keep-alive",
        "5",
        "--timeout",
        "60",
        "--graceful-timeout",
        "10",
        "--log-level",
        "warning",
        "--error-logfile",
        "-",
        "--config",
        "python:bench.gunicorn_config",
    ]

"""Starting and stopping the server processes of a sample."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

import httpx

from bench.profiles import JSON_BODY, PROFILES
from bench.servers import server_command as server_command

ROOT = Path(__file__).resolve().parent.parent


def check_server_log(log: Path) -> None:
    """Reject lifecycle failures even if the response was already sent as 200."""
    markers = (
        "Traceback (most recent call last):",
        "RuntimeWarning:",
        "ResourceWarning:",
        "[ERROR]",
        "Unclosed client",
        "Unclosed connector",
    )
    with log.open() as stream:
        for line in stream:
            if any(marker in line for marker in markers):
                raise RuntimeError(f"Server reported errors; see {log}: {line.strip()}")


def environment(profile: str, *, reset: bool = False) -> dict[str, str]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown framework: {profile}")
    result = {
        **os.environ,
        "BENCH_PROFILE": profile,
        "DJANGO_SETTINGS_MODULE": "bench.settings",
        "PYTHONHASHSEED": "0",
        "PYTHONUNBUFFERED": "1",
    }
    if reset:
        result["BENCH_ALLOW_RESET"] = "1"
    return result


def seed(*, reset: bool, migrate: bool = False) -> None:
    env = environment("django", reset=reset)
    if migrate:
        subprocess.run(
            [sys.executable, "manage.py", "migrate", "--noinput"],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
        )
    subprocess.run(
        [
            sys.executable,
            "manage.py",
            "seed_benchmark",
            *(["--reset"] if reset else []),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=120,
        stdout=subprocess.DEVNULL,
    )


def pinned(command: list[str], cpus: list[int] | None, count: int) -> list[str]:
    """``command`` restricted to the first ``count`` of ``cpus`` with taskset."""
    if not cpus:
        return command
    if len(cpus) < count:
        raise ValueError(f"{count} processes need {count} CPUs; {len(cpus)} given")
    return ["taskset", "-c", ",".join(map(str, cpus[:count])), *command]


def ensure_port_free(host: str, port: int) -> None:
    """Refuse a port another process listens on.

    ``SO_REUSEADDR``, as every server sets it, ignores the connections a
    stopped server left in TIME_WAIT: gunicorn's sync worker closes each
    connection itself, which leaves thousands for a minute.
    """
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        probe.listen()


@contextmanager
def server(
    profile: str,
    *,
    host: str,
    port: int,
    workers: int,
    log: Path,
    server_name: str = "default",
    cpus: list[int] | None = None,
) -> Generator[subprocess.Popen]:
    """
    Start a server and stop its whole process group afterwards; never a process
    found by port or name. With ``cpus``, a server of N workers is pinned to
    the first N of them: one CPU per worker at every worker count.
    """
    if os.name != "posix":
        raise RuntimeError("The benchmark runner requires POSIX process groups")
    ensure_port_free(host, port)
    command = pinned(
        server_command(profile, host, port, workers, server_name), cpus, workers
    )
    with log.open("w") as output:
        child = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment(profile),
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 45
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError(
                            f"{profile} exited during startup; see {log}"
                        )
                    try:
                        response = client.get(f"http://{host}:{port}/json")
                        if response.status_code == 200 and response.json() == JSON_BODY:
                            break
                    except httpx.HTTPError, ValueError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"{profile} startup timed out; see {log}")
            yield child
        finally:
            # The process group belongs to this Popen, including server workers.
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
    check_server_log(log)


@contextmanager
def container_server(
    profile: str,
    *,
    host: str,
    port: int,
    workers: int,
    log: Path,
    image: str,
    cpus: float,
    memory: str,
    server_name: str = "default",
) -> Generator[int]:
    """Linux host networking keeps the same infrastructure addresses in both modes."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError(
            "Docker measurement mode requires a local Linux Docker daemon"
        )
    ensure_port_free(host, port)
    env = environment(profile)
    forwarded = [
        key
        for key in env
        if key.startswith("BENCH_")
        or key in ("PYTHONHASHSEED", "DJANGO_SETTINGS_MODULE")
    ]
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        "aiodrf-bench-" + uuid.uuid4().hex,
        "--network",
        "host",
        "--cpus",
        str(cpus),
        "--memory",
        memory,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    for key in forwarded:
        command += ["--env", key]
    command += [
        image,
        "python",
        *server_command(profile, host, port, workers, server_name)[1:],
    ]
    identifier = subprocess.check_output(
        command, env=env, text=True, timeout=60
    ).strip()
    try:
        state = json.loads(
            subprocess.check_output(
                ["docker", "inspect", identifier], text=True, timeout=15
            )
        )[0]
        deadline = time.monotonic() + 45
        with httpx.Client(timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    response = client.get(f"http://{host}:{port}/json")
                    if response.status_code == 200 and response.json() == JSON_BODY:
                        break
                except httpx.HTTPError, ValueError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError(f"Container startup timed out; see {log}")
        yield state["State"]["Pid"]
    finally:
        subprocess.run(
            ["docker", "stop", "--time", "10", identifier],
            check=False,
            stdout=subprocess.DEVNULL,
            timeout=20,
        )
        with log.open("w") as output:
            subprocess.run(
                ["docker", "logs", identifier],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=15,
            )
        subprocess.run(
            ["docker", "rm", "-f", identifier],
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=20,
        )
    check_server_log(log)

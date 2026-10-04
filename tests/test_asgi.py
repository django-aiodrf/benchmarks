"""The ASGI application of each ASGI profile, driven as a server drives it."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SERVE_ONE_REQUEST = """
import asyncio
from contextlib import asynccontextmanager

import bench.asgi


@asynccontextmanager
async def resources():
    # The service clients are not under test.
    yield object()


bench.asgi.resources = resources


async def main():
    incoming = asyncio.Queue()
    sent = []
    state = {}

    async def send(message):
        sent.append(message)

    lifespan = asyncio.create_task(
        bench.asgi.application({"type": "lifespan", "state": state}, incoming.get, send)
    )
    await incoming.put({"type": "lifespan.startup"})
    while not sent:
        await asyncio.sleep(0.01)
    assert sent[-1]["type"] == "lifespan.startup.complete", sent

    response = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def respond(message):
        response.append(message)

    await bench.asgi.application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/json",
            "raw_path": b"/json",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            # A server gives each request a shallow copy of the lifespan state.
            "state": dict(state),
        },
        receive,
        respond,
    )
    assert response[0]["status"] == 200, response

    await incoming.put({"type": "lifespan.shutdown"})
    await lifespan
    assert sent[-1]["type"] == "lifespan.shutdown.complete", sent


asyncio.run(main())
"""


@pytest.mark.parametrize(
    "profile",
    [
        "aiodrf",
        "aiodrf-tuned",
        "aiodrf-fastdrf",
        "django",
        "ninja",
        "fastapi",
        "litestar",
    ],
)
def test_profile_serves_a_request_within_its_lifespan(profile):
    environment = {
        **os.environ,
        "BENCH_PROFILE": profile,
        "BENCH_DATABASE": "sqlite",
        "DJANGO_SETTINGS_MODULE": "bench.settings",
    }
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", textwrap.dedent(SERVE_ONE_REQUEST)],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr

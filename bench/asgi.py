"""ASGI lifespans: SQLAlchemy for FastAPI/Litestar, Django for Django profiles."""

import asyncio
import os
from contextlib import asynccontextmanager, nullcontext

from django.core.asgi import get_asgi_application

from bench.services import resources

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")
PROFILE = os.environ.get("BENCH_PROFILE")
django_application = get_asgi_application()


if PROFILE in ("aiodrf-tuned", "aiodrf-fastdrf"):
    # aiodrf's application, for its opt-in AIODRF["REQUEST_THREADS"]. It
    # starts and stops its request threads in its own lifespan, which
    # ``application`` runs inside the benchmark's.
    from aiodrf.asgi import get_asgi_application as get_aiodrf_application

    http_application = get_aiodrf_application(lifespan=None)
elif PROFILE == "fastapi":
    from bench.adapters.fastapi import app

    http_application = app
elif PROFILE == "litestar":
    from bench.adapters.litestar import app

    http_application = app
else:
    http_application = django_application


@asynccontextmanager
async def application_lifespan(app, state):
    """Run ``app``'s lifespan protocol on ``state``, as a server would."""
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    task = asyncio.create_task(
        app({"type": "lifespan", "state": state}, incoming.get, outgoing.put)
    )
    await incoming.put({"type": "lifespan.startup"})
    message = await outgoing.get()
    if message["type"] != "lifespan.startup.complete":
        await task
        raise RuntimeError(message.get("message", message["type"]))
    try:
        yield
    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        message = await outgoing.get()
        await task
        if message["type"] != "lifespan.shutdown.complete":
            raise RuntimeError(message.get("message", message["type"]))


async def application(scope, receive, send):
    if scope["type"] != "lifespan":
        return await http_application(scope, receive, send)
    message = await receive()
    if message["type"] != "lifespan.startup":
        raise ValueError("Expected lifespan startup")
    try:
        inner_lifespan = (
            application_lifespan(http_application, scope["state"])
            if PROFILE in ("aiodrf-tuned", "aiodrf-fastdrf", "fastapi", "litestar")
            else nullcontext()
        )
        service_context = (
            resources()
            if os.environ.get("BENCH_SERVICE_CLIENTS", "1") == "1"
            else nullcontext()
        )
        async with service_context as clients, inner_lifespan:
            scope["state"]["resources"] = clients
            await send({"type": "lifespan.startup.complete"})
            message = await receive()
            if message["type"] != "lifespan.shutdown":
                raise ValueError("Expected lifespan shutdown")
        scope["state"].pop("resources", None)
        await send({"type": "lifespan.shutdown.complete"})
    except Exception as exc:
        kind = "startup" if message["type"] == "lifespan.startup" else "shutdown"
        await send({"type": f"lifespan.{kind}.failed", "message": str(exc)})
        raise

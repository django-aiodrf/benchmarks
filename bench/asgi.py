"""The ASGI application of every ASGI profile, with worker-owned service clients.

The lifespan opens the benchmark's service clients once per worker and stores
them in the lifespan state, which each request's scope carries. FastAPI and
Litestar applications use the Django ORM: they are wrapped to send Django's
``request_started`` and ``request_finished`` signals around each request, as
Django's own ASGI handler does, so that database connections are handled the
same way in every profile.
"""

import os

from django.core.asgi import get_asgi_application

from bench.services import resources

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")
PROFILE = os.environ.get("BENCH_PROFILE")
django_application = get_asgi_application()


def with_django_request_signals(app):
    from django.core import signals

    async def wrapped(scope, receive, send):
        await signals.request_started.asend(sender=wrapped, scope=scope)
        try:
            await app(scope, receive, send)
        finally:
            await signals.request_finished.asend(sender=wrapped)

    return wrapped


if PROFILE == "aiodrf-tuned":
    # aiodrf's application, for its opt-in AIODRF["REQUEST_THREADS"].
    from aiodrf.asgi import get_asgi_application as get_aiodrf_application

    http_application = get_aiodrf_application(lifespan=None)
elif PROFILE == "fastapi":
    from bench.adapters.fastapi import app

    http_application = with_django_request_signals(app)
elif PROFILE == "litestar":
    from bench.adapters.litestar import app

    http_application = with_django_request_signals(app)
else:
    http_application = django_application


async def application(scope, receive, send):
    if scope["type"] != "lifespan":
        return await http_application(scope, receive, send)
    message = await receive()
    if message["type"] != "lifespan.startup":
        raise ValueError("Expected lifespan startup")
    try:
        async with resources() as clients:
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

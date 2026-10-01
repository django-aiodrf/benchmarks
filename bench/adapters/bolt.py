"""Django Bolt: async handlers on its own server, with msgspec validation."""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import msgspec
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django_bolt import BoltAPI
from django_bolt.auth import IsAuthenticated, JWTAuthentication
from django_bolt.exceptions import HTTPException
from django_bolt.params import Depends
from django_bolt.responses import JSON, StreamingResponse

from bench.app.models import Article, Author
from bench.auth import ALGORITHM, KEY
from bench.domain import (
    InvalidReferences,
    article_page_async,
    article_queryset,
    article_values,
    create_article,
)
from bench.profiles import (
    AUTH_PAGE_SIZE,
    JSON_10K_BODY,
    JSON_BODY,
    SERVICE_SCENARIOS,
    SERVICE_WRITES,
    status_for,
    stream_items,
)
from bench.services import resources


def close_database_pools():
    """
    Bolt's parent process checks migrations, then closes its connections and
    forks the workers. With Django's connection pool, ``close()`` returns a
    connection to the pool, whose sockets the workers would then share.
    Close the pools before each fork, so that each worker opens its own.
    """
    from django.db import connections

    for connection in connections.all(initialized_only=True):
        close_pool = getattr(connection, "close_pool", None)
        if close_pool is not None:
            close_pool()
        else:
            connection.close()


os.register_at_fork(before=close_database_pools)


@asynccontextmanager
async def lifespan(app: BoltAPI) -> AsyncGenerator[None]:
    async with resources() as clients:
        app.benchmark_resources = clients
        app.benchmark_loop = asyncio.get_running_loop()
        try:
            yield
        finally:
            del app.benchmark_resources
            del app.benchmark_loop


async def execute_service(scenario: str) -> dict:
    """Keep pooled clients on their lifespan loop, including during shutdown.

    Bolt 0.11.1 dispatches HTTP on a different WorkerLoop from its lifespan.
    The explicit cross-loop submission is part of the measured Bolt adapter.
    No driver internals or framework lifecycle methods are modified.
    """
    operation = api.benchmark_resources.aexecute(scenario)
    loop = api.benchmark_loop
    if asyncio.get_running_loop() is loop:
        return await operation
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(operation, loop))


api = BoltAPI(
    lifespan=lifespan, enable_logging=False, compression=False, django_middleware=False
)


class ArticleInput(msgspec.Struct):
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=120)]
    body: Annotated[str, msgspec.Meta(min_length=1)]
    author_id: Annotated[int, msgspec.Meta(ge=1)]
    tag_ids: Annotated[
        list[Annotated[int, msgspec.Meta(ge=1)]], msgspec.Meta(max_length=5)
    ]


@api.get("/json")
async def json_view() -> dict:
    return JSON_BODY


@api.get("/json-10k")
async def large_json_view() -> dict:
    return JSON_10K_BODY


@api.get("/db")
async def database_view() -> list[dict]:
    return [
        {"id": row.id, "name": row.name}
        async for row in Author.objects.order_by("id")[:10]
    ]


@api.get("/articles")
async def articles_view() -> dict:
    page = await article_page_async()
    return {
        "total": page["total"],
        "items": [article_values(row) for row in page["items"]],
    }


@api.post("/articles", status_code=201)
async def create_view(data: ArticleInput) -> dict:
    try:
        article = await sync_to_async(create_article)(msgspec.to_builtins(data))
    except InvalidReferences as exc:
        raise HTTPException(400, str(exc)) from exc
    return article_values(article)


@api.get("/articles/{pk}")
async def article_view(pk: int) -> dict:
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise HTTPException(404, "Article not found") from exc
    return article_values(article)


@api.get("/workloads/{scenario}")
async def service_view(scenario: str) -> dict:
    if scenario not in SERVICE_SCENARIOS:
        raise HTTPException(404, "Unknown workload")
    if scenario in SERVICE_WRITES:
        raise HTTPException(405, "Use POST for this workload")
    return await execute_service(scenario)


# The response object selects 200/201; it is not a data-model annotation.
@api.post("/workloads/{scenario}", response_model=None)
async def service_write(scenario: str) -> JSON:
    if scenario not in SERVICE_WRITES:
        raise HTTPException(
            405 if scenario in SERVICE_SCENARIOS else 404, "Not a write workload"
        )
    return JSON(
        await execute_service(scenario),
        status_code=status_for(scenario),
    )


async def ndjson(items):
    for item in items:
        yield msgspec.json.encode(item) + b"\n"


@api.get("/stream-1k")
async def stream_1k() -> StreamingResponse:
    return StreamingResponse(
        ndjson(stream_items("stream-1k")), media_type="application/x-ndjson"
    )


@api.get("/stream-10k")
async def stream_10k() -> StreamingResponse:
    return StreamingResponse(
        ndjson(stream_items("stream-10k")), media_type="application/x-ndjson"
    )


# Bolt validates the token in Rust. The user is loaded with Django's async ORM,
# as in the other asynchronous profiles, not with Bolt's `get_current_user`:
# in django-bolt 0.11.1 that loads it on the default executor of up to 32
# threads, each of which keeps a pooled connection, so it exhausts the
# psycopg pool of 10 that Bolt's deployment guide recommends
# (docs/frameworks.md).
JWT = [JWTAuthentication(secret=KEY, algorithms=[ALGORITHM])]
AUTHENTICATED = [IsAuthenticated()]


async def current_user(request):
    user_id = request.get("context", {}).get("user_id")
    if not user_id:
        return None
    return await User.objects.filter(pk=user_id).afirst()


CURRENT_USER = Depends(current_user)


@api.get("/auth/articles", auth=JWT, guards=AUTHENTICATED)
async def authenticated_articles(page: int = 1) -> dict:
    offset = (page - 1) * AUTH_PAGE_SIZE
    count = await Article.objects.acount()
    items = [row async for row in article_queryset()[offset : offset + AUTH_PAGE_SIZE]]
    return {"count": count, "items": [article_values(row) for row in items]}


@api.get("/auth/articles/{pk}", auth=JWT, guards=AUTHENTICATED)
async def authenticated_article(pk: int, user=CURRENT_USER) -> dict:
    if user is None:
        raise HTTPException(401, "Authentication credentials were not provided.")
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise HTTPException(404, "Article not found") from exc
    return {
        "user": {"id": user.id, "username": user.username},
        "article": article_values(article),
    }

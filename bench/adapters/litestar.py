"""Litestar with msgspec validation and serialization, on the Django ORM."""

import json
from typing import Annotated, Any

import msgspec
from asgiref.sync import sync_to_async
from litestar import Litestar, Request, Response, get, post
from litestar.di import Provide
from litestar.exceptions import HTTPException, NotAuthorizedException, NotFoundException
from litestar.pagination import OffsetPagination
from litestar.response import Stream

from bench.app.models import Article, Author
from bench.auth import auser, bearer
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
from bench.services import request_resources


class ArticleInput(msgspec.Struct):
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=120)]
    body: Annotated[str, msgspec.Meta(min_length=1)]
    author_id: Annotated[int, msgspec.Meta(ge=1)]
    tag_ids: Annotated[
        list[Annotated[int, msgspec.Meta(ge=1)]], msgspec.Meta(max_length=5)
    ]


@get("/json")
async def json_view() -> dict:
    return JSON_BODY


@get("/json-10k")
async def large_json_view() -> dict:
    return JSON_10K_BODY


@get("/db")
async def database_view() -> list[dict]:
    return [
        {"id": row.id, "name": row.name}
        async for row in Author.objects.order_by("id")[:10]
    ]


@get("/articles")
async def articles_view() -> dict:
    page = await article_page_async()
    return {
        "total": page["total"],
        "items": [article_values(row) for row in page["items"]],
    }


@post("/articles")
async def create_view(data: ArticleInput) -> dict:
    try:
        article = await sync_to_async(create_article)(msgspec.to_builtins(data))
    except InvalidReferences as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return article_values(article)


@get("/articles/{pk:int}")
async def article_view(pk: int) -> dict:
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise NotFoundException("Article not found") from exc
    return article_values(article)


@get("/workloads/{scenario:str}")
async def service_view(request: Request, scenario: str) -> dict:
    if scenario not in SERVICE_SCENARIOS:
        raise NotFoundException("Unknown workload")
    if scenario in SERVICE_WRITES:
        raise HTTPException(status_code=405, detail="Use POST for this workload")
    return await request_resources(request).aexecute(scenario)


@post("/workloads/{scenario:str}")
async def service_write(request: Request, scenario: str) -> Response[dict]:
    if scenario not in SERVICE_WRITES:
        raise HTTPException(
            status_code=405 if scenario in SERVICE_SCENARIOS else 404,
            detail="Not a write workload",
        )
    return Response(
        await request_resources(request).aexecute(scenario),
        status_code=status_for(scenario),
    )


async def ndjson(items):
    for item in items:
        yield json.dumps(item, separators=(",", ":")).encode() + b"\n"


@get("/stream-1k")
async def stream_1k() -> Stream:
    return Stream(ndjson(stream_items("stream-1k")), media_type="application/x-ndjson")


@get("/stream-10k")
async def stream_10k() -> Stream:
    return Stream(ndjson(stream_items("stream-10k")), media_type="application/x-ndjson")


async def current_user(request: Request) -> Any:
    token = bearer(request.headers.get("authorization", ""))
    user = await auser(token) if token else None
    if user is None:
        raise NotAuthorizedException(headers={"WWW-Authenticate": "Bearer"})
    return user


@get("/auth/articles", dependencies={"user": Provide(current_user)})
async def authenticated_articles(user: Any, page: int = 1) -> OffsetPagination[dict]:
    offset = (page - 1) * AUTH_PAGE_SIZE
    total = await Article.objects.acount()
    items = [row async for row in article_queryset()[offset : offset + AUTH_PAGE_SIZE]]
    return OffsetPagination(
        items=[article_values(row) for row in items],
        limit=AUTH_PAGE_SIZE,
        offset=offset,
        total=total,
    )


@get("/auth/articles/{pk:int}", dependencies={"user": Provide(current_user)})
async def authenticated_article(user: Any, pk: int) -> dict:
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise NotFoundException("Article not found") from exc
    return {
        "user": {"id": user.id, "username": user.username},
        "article": article_values(article),
    }


app = Litestar(
    route_handlers=[
        json_view,
        large_json_view,
        database_view,
        articles_view,
        create_view,
        article_view,
        service_view,
        service_write,
        stream_1k,
        stream_10k,
        authenticated_articles,
        authenticated_article,
    ],
    openapi_config=None,
    debug=False,
)

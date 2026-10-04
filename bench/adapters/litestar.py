"""Litestar with msgspec validation and serialization, on SQLAlchemy async ORM."""

import json
from typing import Annotated, Any

import msgspec
from litestar import Litestar, Request, Response, get, post
from litestar.di import NamedDependency, Provide
from litestar.exceptions import HTTPException, NotAuthorizedException, NotFoundException
from litestar.pagination import OffsetPagination
from litestar.params import FromPath, FromQuery
from litestar.response import Stream
from sqlalchemy.ext.asyncio import AsyncSession

from bench import sqlalchemy_store as store
from bench.auth import bearer
from bench.domain import InvalidReferences
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


async def session_dependency(request: Request):
    async with request.app.state.sessions() as session:
        yield session


class Message(msgspec.Struct):
    message: str


class NamedObject(msgspec.Struct):
    id: int
    name: str


class ArticleOutput(msgspec.Struct):
    id: int
    title: str
    body: str
    author: NamedObject
    tags: list[NamedObject]


class ArticlePage(msgspec.Struct):
    total: int
    items: list[ArticleOutput]


class UserOutput(msgspec.Struct):
    id: int
    username: str


class AuthenticatedArticle(msgspec.Struct):
    user: UserOutput
    article: ArticleOutput


def article_output(row) -> ArticleOutput:
    return ArticleOutput(
        row.id,
        row.title,
        row.body,
        NamedObject(row.author.id, row.author.name),
        [NamedObject(tag.id, tag.name) for tag in row.tags],
    )


class ArticleInput(msgspec.Struct):
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=120)]
    body: Annotated[str, msgspec.Meta(min_length=1)]
    author_id: Annotated[int, msgspec.Meta(ge=1)]
    tag_ids: Annotated[
        list[Annotated[int, msgspec.Meta(ge=1)]], msgspec.Meta(max_length=5)
    ]


@get("/json")
async def json_view() -> Message:
    return Message(**JSON_BODY)


@get("/json-10k")
async def large_json_view() -> Message:
    return Message(**JSON_10K_BODY)


@get("/db")
async def database_view(session: NamedDependency[AsyncSession]) -> list[NamedObject]:
    return [NamedObject(row.id, row.name) for row in await store.authors(session)]


@get("/articles")
async def articles_view(session: NamedDependency[AsyncSession]) -> ArticlePage:
    page = await store.page(session)
    return ArticlePage(page["total"], [article_output(row) for row in page["items"]])


@post("/articles")
async def create_view(
    data: ArticleInput, session: NamedDependency[AsyncSession]
) -> ArticleOutput:
    try:
        article = await store.create_article(session, msgspec.to_builtins(data))
    except InvalidReferences as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return article_output(article)


@get("/articles/{pk:int}")
async def article_view(
    pk: FromPath[int], session: NamedDependency[AsyncSession]
) -> ArticleOutput:
    article = await store.article(session, pk)
    if article is None:
        raise NotFoundException("Article not found")
    return article_output(article)


@get("/workloads/{scenario:str}")
async def service_view(request: Request, scenario: FromPath[str]) -> dict:
    if scenario not in SERVICE_SCENARIOS:
        raise NotFoundException("Unknown workload")
    if scenario in SERVICE_WRITES:
        raise HTTPException(status_code=405, detail="Use POST for this workload")
    return await request_resources(request).aexecute(scenario)


@post("/workloads/{scenario:str}")
async def service_write(request: Request, scenario: FromPath[str]) -> Response[dict]:
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


async def current_user(request: Request, session: NamedDependency[AsyncSession]) -> Any:
    token = bearer(request.headers.get("authorization", ""))
    user = await store.authenticated_user(session, token) if token else None
    if user is None:
        raise NotAuthorizedException(headers={"WWW-Authenticate": "Bearer"})
    return user


@get("/auth/articles", dependencies={"user": Provide(current_user)})
async def authenticated_articles(
    user: NamedDependency[Any],
    session: NamedDependency[AsyncSession],
    page: FromQuery[int] = 1,
) -> OffsetPagination[ArticleOutput]:
    offset = (page - 1) * AUTH_PAGE_SIZE
    result = await store.page(session, page, AUTH_PAGE_SIZE)
    total, items = result["total"], result["items"]
    return OffsetPagination(
        items=[article_output(row) for row in items],
        limit=AUTH_PAGE_SIZE,
        offset=offset,
        total=total,
    )


@get("/auth/articles/{pk:int}", dependencies={"user": Provide(current_user)})
async def authenticated_article(
    user: NamedDependency[Any],
    pk: FromPath[int],
    session: NamedDependency[AsyncSession],
) -> AuthenticatedArticle:
    article = await store.article(session, pk)
    if article is None:
        raise NotFoundException("Article not found")
    return AuthenticatedArticle(
        UserOutput(user.id, user.username), article_output(article)
    )


app = Litestar(
    lifespan=[store.database_lifespan],
    dependencies={"session": Provide(session_dependency)},
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

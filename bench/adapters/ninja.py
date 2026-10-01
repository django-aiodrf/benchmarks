"""Django Ninja's Pydantic validation and response serialization."""

import json
from typing import Annotated

from asgiref.sync import sync_to_async
from django.http import JsonResponse, StreamingHttpResponse
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate
from ninja.security import HttpBearer
from pydantic import Field

from bench.app.models import Article, Author
from bench.auth import auser
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

api = NinjaAPI(docs_url=None, openapi_url=None)


@api.get("/workloads/{scenario}")
async def service_view(request, scenario: str):
    if scenario not in SERVICE_SCENARIOS:
        raise HttpError(404, "Unknown workload")
    if scenario in SERVICE_WRITES:
        raise HttpError(405, "Use POST for this workload")
    return await request_resources(request).aexecute(scenario)


@api.post("/workloads/{scenario}")
async def service_write(request, scenario: str):
    if scenario not in SERVICE_WRITES:
        raise HttpError(
            405 if scenario in SERVICE_SCENARIOS else 404, "Not a write workload"
        )
    return JsonResponse(
        await request_resources(request).aexecute(scenario), status=status_for(scenario)
    )


class Message(Schema):
    message: str


class NamedObject(Schema):
    id: int
    name: str


class ArticleOutput(Schema):
    id: int
    title: str
    body: str
    author: NamedObject
    tags: list[NamedObject]


class ArticlePage(Schema):
    total: int
    items: list[ArticleOutput]


class ArticleInput(Schema):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1)
    author_id: int = Field(ge=1)
    tag_ids: list[Annotated[int, Field(ge=1)]] = Field(max_length=5)


@api.get("/json", response=Message)
async def json_view(request):
    return JSON_BODY


@api.get("/json-10k", response=Message)
async def large_json_view(request):
    return JSON_10K_BODY


@api.get("/db", response=list[NamedObject])
async def database_view(request):
    return [
        {"id": row.id, "name": row.name}
        async for row in Author.objects.order_by("id")[:10]
    ]


@api.get("/articles", response=ArticlePage)
async def articles_view(request):
    page = await article_page_async()
    return {
        "total": page["total"],
        "items": [article_values(row) for row in page["items"]],
    }


@api.post("/articles", response={201: ArticleOutput})
async def create_view(request, data: ArticleInput):
    try:
        article = await sync_to_async(create_article)(data.model_dump())
    except InvalidReferences as exc:
        raise HttpError(400, str(exc)) from exc
    return 201, article_values(article)


@api.get("/articles/{pk}", response=ArticleOutput)
async def article_view(request, pk: int):
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise HttpError(404, "Article not found") from exc
    return article_values(article)


async def ndjson(items):
    for item in items:
        yield json.dumps(item, separators=(",", ":")).encode() + b"\n"


@api.get("/stream-1k")
async def stream_1k(request):
    return StreamingHttpResponse(
        ndjson(stream_items("stream-1k")), content_type="application/x-ndjson"
    )


@api.get("/stream-10k")
async def stream_10k(request):
    return StreamingHttpResponse(
        ndjson(stream_items("stream-10k")), content_type="application/x-ndjson"
    )


class JWTBearer(HttpBearer):
    async def authenticate(self, request, token):
        return await auser(token)


jwt_auth = JWTBearer()


class UserOutput(Schema):
    id: int
    username: str


class AuthenticatedArticle(Schema):
    user: UserOutput
    article: ArticleOutput


@api.get("/auth/articles", response=list[ArticleOutput], auth=jwt_auth)
@paginate(PageNumberPagination, page_size=AUTH_PAGE_SIZE)
async def authenticated_articles(request):
    return article_queryset()


@api.get("/auth/articles/{pk}", response=AuthenticatedArticle, auth=jwt_auth)
async def authenticated_article(request, pk: int):
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise HttpError(404, "Article not found") from exc
    return {"user": request.auth, "article": article_values(article)}

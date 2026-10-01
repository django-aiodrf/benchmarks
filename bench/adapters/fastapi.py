"""FastAPI with Pydantic validation and response models, on the Django ORM."""

import json
from typing import Annotated

from asgiref.sync import sync_to_async
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

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

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Message(BaseModel):
    message: str


class NamedObject(BaseModel):
    id: int
    name: str


class ArticleOutput(BaseModel):
    id: int
    title: str
    body: str
    author: NamedObject
    tags: list[NamedObject]


class ArticlePage(BaseModel):
    total: int
    items: list[ArticleOutput]


class ArticleInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1)
    author_id: int = Field(ge=1)
    tag_ids: list[Annotated[int, Field(ge=1)]] = Field(max_length=5)


class UserOutput(BaseModel):
    id: int
    username: str


class AuthenticatedArticle(BaseModel):
    user: UserOutput
    article: ArticleOutput


class AuthenticatedPage(BaseModel):
    count: int
    items: list[ArticleOutput]


@app.get("/json", response_model=Message)
async def json_view():
    return JSON_BODY


@app.get("/json-10k", response_model=Message)
async def large_json_view():
    return JSON_10K_BODY


@app.get("/db", response_model=list[NamedObject])
async def database_view():
    return [
        {"id": row.id, "name": row.name}
        async for row in Author.objects.order_by("id")[:10]
    ]


@app.get("/articles", response_model=ArticlePage)
async def articles_view():
    page = await article_page_async()
    return {
        "total": page["total"],
        "items": [article_values(row) for row in page["items"]],
    }


@app.post("/articles", response_model=ArticleOutput, status_code=201)
async def create_view(data: ArticleInput):
    try:
        article = await sync_to_async(create_article)(data.model_dump())
    except InvalidReferences as exc:
        raise HTTPException(400, str(exc)) from exc
    return article_values(article)


@app.get("/articles/{pk}", response_model=ArticleOutput)
async def article_view(pk: int):
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise HTTPException(404, "Article not found") from exc
    return article_values(article)


@app.get("/workloads/{scenario}")
async def service_view(request: Request, scenario: str):
    if scenario not in SERVICE_SCENARIOS:
        raise HTTPException(404, "Unknown workload")
    if scenario in SERVICE_WRITES:
        raise HTTPException(405, "Use POST for this workload")
    return await request_resources(request).aexecute(scenario)


@app.post("/workloads/{scenario}")
async def service_write(request: Request, scenario: str):
    if scenario not in SERVICE_WRITES:
        raise HTTPException(
            405 if scenario in SERVICE_SCENARIOS else 404, "Not a write workload"
        )
    return JSONResponse(
        await request_resources(request).aexecute(scenario),
        status_code=status_for(scenario),
    )


async def ndjson(items):
    for item in items:
        yield json.dumps(item, separators=(",", ":")).encode() + b"\n"


@app.get("/stream-1k")
async def stream_1k():
    return StreamingResponse(
        ndjson(stream_items("stream-1k")), media_type="application/x-ndjson"
    )


@app.get("/stream-10k")
async def stream_10k():
    return StreamingResponse(
        ndjson(stream_items("stream-10k")), media_type="application/x-ndjson"
    )


bearer = HTTPBearer(auto_error=False)


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    user = await auser(credentials.credentials) if credentials else None
    if user is None:
        raise HTTPException(
            401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
        )
    return user


@app.get("/auth/articles", response_model=AuthenticatedPage)
async def authenticated_articles(
    user: Annotated[object, Depends(current_user)], page: int = 1
):
    offset = (page - 1) * AUTH_PAGE_SIZE
    count = await Article.objects.acount()
    items = [row async for row in article_queryset()[offset : offset + AUTH_PAGE_SIZE]]
    return {"count": count, "items": [article_values(row) for row in items]}


@app.get("/auth/articles/{pk}", response_model=AuthenticatedArticle)
async def authenticated_article(
    pk: int, user: Annotated[object, Depends(current_user)]
):
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise HTTPException(404, "Article not found") from exc
    return {
        "user": {"id": user.id, "username": user.username},
        "article": article_values(article),
    }

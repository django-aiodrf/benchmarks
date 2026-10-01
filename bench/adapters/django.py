"""Plain Django: function views and JsonResponse, synchronous and async."""

import json

from asgiref.sync import sync_to_async
from django.http import (
    Http404,
    HttpResponseNotAllowed,
    JsonResponse,
    StreamingHttpResponse,
)

from bench.app.models import Article, Author
from bench.auth import auser, bearer, user
from bench.domain import (
    InvalidReferences,
    article_page,
    article_page_async,
    article_queryset,
    article_values,
    create_article,
)
from bench.profiles import (
    AUTH_PAGE_SIZE,
    JSON_10K_BODY,
    JSON_BODY,
    method_for,
    status_for,
    stream_items,
)
from bench.services import request_resources


def service_view(request, scenario):
    method = method_for(scenario)
    if request.method != method:
        return HttpResponseNotAllowed([method])
    return response(
        request_resources(request).execute(scenario), status=status_for(scenario)
    )


async def async_service_view(request, scenario):
    method = method_for(scenario)
    if request.method != method:
        return HttpResponseNotAllowed([method])
    return response(
        await request_resources(request).aexecute(scenario), status=status_for(scenario)
    )


def validate_input(body: bytes) -> dict:
    """Validate the canonical JSON contract without an external schema library."""
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("Expected an object")
    title, content = data.get("title"), data.get("body")
    if not isinstance(title, str) or not 1 <= len(title) <= 120:
        raise ValueError("Invalid title")
    if not isinstance(content, str) or not content:
        raise ValueError("Invalid body")
    author, tags = data.get("author_id"), data.get("tag_ids")
    if type(author) is not int or author < 1:
        raise ValueError("Invalid author")
    if not isinstance(tags, list) or len(tags) > 5:
        raise ValueError("Invalid tags")
    if any(type(tag) is not int or tag < 1 for tag in tags):
        raise ValueError("Invalid tag identifier")
    return {"title": title, "body": content, "author_id": author, "tag_ids": tags}


def response(value, *, status=200):
    return JsonResponse(
        value,
        safe=isinstance(value, dict),
        status=status,
        json_dumps_params={"separators": (",", ":")},
    )


def json_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return response(JSON_BODY)


def large_json_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return response(JSON_10K_BODY)


async def async_json_view(request):
    return json_view(request)


async def async_large_json_view(request):
    return large_json_view(request)


def database_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return response(
        [{"id": row.id, "name": row.name} for row in Author.objects.order_by("id")[:10]]
    )


async def async_database_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return response(
        [
            {"id": row.id, "name": row.name}
            async for row in Author.objects.order_by("id")[:10]
        ]
    )


def articles_view(request):
    if request.method == "GET":
        page = article_page()
        return response(
            {
                "total": page["total"],
                "items": [article_values(row) for row in page["items"]],
            }
        )
    if request.method == "POST":
        try:
            article = create_article(validate_input(request.body))
        except (ValueError, InvalidReferences) as exc:
            return response({"detail": str(exc)}, status=400)
        return response(article_values(article), status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


async def async_articles_view(request):
    if request.method == "GET":
        page = await article_page_async()
        return response(
            {
                "total": page["total"],
                "items": [article_values(row) for row in page["items"]],
            }
        )
    if request.method == "POST":
        try:
            article = await sync_to_async(create_article)(validate_input(request.body))
        except (ValueError, InvalidReferences) as exc:
            return response({"detail": str(exc)}, status=400)
        return response(article_values(article), status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


def article_view(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        article = article_queryset().get(pk=pk)
    except Article.DoesNotExist as exc:
        raise Http404 from exc
    return response(article_values(article))


async def async_article_view(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise Http404 from exc
    return response(article_values(article))


NDJSON = "application/x-ndjson"


def _line(item) -> bytes:
    return json.dumps(item, separators=(",", ":")).encode() + b"\n"


def stream_view(request, scenario):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    lines = (_line(item) for item in stream_items(scenario))
    return StreamingHttpResponse(lines, content_type=NDJSON)


async def async_stream_view(request, scenario):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    async def lines():
        for item in stream_items(scenario):
            yield _line(item)

    return StreamingHttpResponse(lines(), content_type=NDJSON)


def unauthorized():
    result = response(
        {"detail": "Authentication credentials were not provided."}, status=401
    )
    result["WWW-Authenticate"] = "Bearer"
    return result


def _page_number(request) -> int:
    try:
        return max(1, int(request.GET.get("page", "1")))
    except ValueError:
        return 1


def _user_values(found) -> dict:
    return {"id": found.id, "username": found.username}


def authenticated_article_view(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    value = bearer(request.headers.get("Authorization", ""))
    found = user(value) if value else None
    if found is None:
        return unauthorized()
    try:
        article = article_queryset().get(pk=pk)
    except Article.DoesNotExist as exc:
        raise Http404 from exc
    return response({"user": _user_values(found), "article": article_values(article)})


async def async_authenticated_article_view(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    value = bearer(request.headers.get("Authorization", ""))
    found = await auser(value) if value else None
    if found is None:
        return unauthorized()
    try:
        article = await article_queryset().aget(pk=pk)
    except Article.DoesNotExist as exc:
        raise Http404 from exc
    return response({"user": _user_values(found), "article": article_values(article)})


def authenticated_articles_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    value = bearer(request.headers.get("Authorization", ""))
    if value is None or user(value) is None:
        return unauthorized()
    offset = (_page_number(request) - 1) * AUTH_PAGE_SIZE
    count = Article.objects.count()
    items = article_queryset()[offset : offset + AUTH_PAGE_SIZE]
    return response({"count": count, "items": [article_values(row) for row in items]})


async def async_authenticated_articles_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    value = bearer(request.headers.get("Authorization", ""))
    if value is None or await auser(value) is None:
        return unauthorized()
    offset = (_page_number(request) - 1) * AUTH_PAGE_SIZE
    count = await Article.objects.acount()
    items = [row async for row in article_queryset()[offset : offset + AUTH_PAGE_SIZE]]
    return response({"count": count, "items": [article_values(row) for row in items]})

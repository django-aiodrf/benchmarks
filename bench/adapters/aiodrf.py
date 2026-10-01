"""aiodrf: async APIViews with DRF serializers awaited through ``aiodrf.aio``."""

from aiodrf import aio, authentication, generics
from aiodrf import serializers as serializer_module
from aiodrf.permissions import IsAuthenticated
from aiodrf.response import DataResponse, StreamingResponse
from aiodrf.views import APIView
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import Http404
from rest_framework.exceptions import (
    AuthenticationFailed,
    MethodNotAllowed,
    ValidationError,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response as DRFResponse
from rest_framework.settings import api_settings

from bench.adapters.serializers import build_serializers
from bench.app.models import Article, Author
from bench.auth import auser, bearer
from bench.domain import (
    InvalidReferences,
    article_page_async,
    article_queryset,
    create_article,
)
from bench.profiles import (
    AUTH_PAGE_SIZE,
    JSON_10K_BODY,
    JSON_BODY,
    SERVICE_WRITES,
    status_for,
    stream_items,
)
from bench.services import request_resources

serializers = build_serializers(serializer_module)

# The tuned profile opts in to aiodrf's DataResponse: DRF's JSON content in
# Django's HttpResponse, without DRF's template response.
Response = DataResponse if settings.PROFILE == "aiodrf-tuned" else DRFResponse


class ServiceView(APIView):
    async def get(self, request, scenario):
        if scenario in SERVICE_WRITES:
            raise MethodNotAllowed("GET")
        return Response(await request_resources(request).aexecute(scenario))

    async def post(self, request, scenario):
        if scenario not in SERVICE_WRITES:
            raise MethodNotAllowed("POST")
        return Response(
            await request_resources(request).aexecute(scenario),
            status=status_for(scenario),
        )


class JsonView(APIView):
    async def get(self, request):
        return Response(JSON_BODY)


class LargeJsonView(APIView):
    async def get(self, request):
        return Response(JSON_10K_BODY)


class DatabaseView(APIView):
    async def get(self, request):
        rows = [row async for row in Author.objects.order_by("id")[:10]]
        return Response(await aio.data(serializers.author(rows, many=True)))


class ArticlesView(APIView):
    async def get(self, request):
        page = await article_page_async()
        return Response(
            {
                "total": page["total"],
                "items": await aio.data(serializers.article(page["items"], many=True)),
            }
        )

    async def post(self, request):
        serializer = serializers.create(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        try:
            article = await sync_to_async(create_article)(serializer.validated_data)
        except InvalidReferences as exc:
            raise ValidationError(str(exc)) from exc
        return Response(await aio.data(serializers.article(article)), status=201)


class ArticleView(APIView):
    async def get(self, request, pk):
        try:
            article = await article_queryset().aget(pk=pk)
        except Article.DoesNotExist as exc:
            raise Http404 from exc
        return Response(await aio.data(serializers.article(article)))


async def _items(items):
    for item in items:
        yield item


class StreamView(APIView):
    async def get(self, request, scenario):
        # The profile's renderer: DRF's, or msgspec's in the tuned profile.
        renderer = api_settings.DEFAULT_RENDERER_CLASSES[0]()
        return StreamingResponse(_items(stream_items(scenario)), renderer=renderer)


class JWTAuthentication(authentication.BaseAuthentication):
    async def aauthenticate(self, request):
        value = bearer(request.headers.get("Authorization", ""))
        if value is None:
            return None
        user = await auser(value)
        if user is None:
            raise AuthenticationFailed("Invalid token.")
        return user, value

    async def aauthenticate_header(self, request):
        return "Bearer"


class Authenticated:
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]


class AuthenticatedArticleView(Authenticated, APIView):
    async def get(self, request, pk):
        try:
            article = await article_queryset().aget(pk=pk)
        except Article.DoesNotExist as exc:
            raise Http404 from exc
        return Response(
            {
                "user": await aio.data(serializers.user(request.user)),
                "article": await aio.data(serializers.article(article)),
            }
        )


class ArticlePagination(PageNumberPagination):
    page_size = AUTH_PAGE_SIZE


class AuthenticatedArticlesView(Authenticated, generics.ListAPIView):
    serializer_class = serializers.article
    pagination_class = ArticlePagination

    def get_queryset(self):
        return article_queryset()

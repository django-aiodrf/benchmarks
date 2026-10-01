"""adrf: async APIViews with adrf serializers (``.adata``)."""

from adrf import generics
from adrf import serializers as serializer_module
from adrf.views import APIView
from asgiref.sync import sync_to_async
from django.http import Http404, StreamingHttpResponse
from rest_framework.exceptions import MethodNotAllowed, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from bench.adapters.drf_common import ArticlePagination, JWTAuthentication
from bench.adapters.serializers import build_serializers
from bench.app.models import Article, Author
from bench.domain import (
    InvalidReferences,
    article_page_async,
    article_queryset,
    create_article,
)
from bench.profiles import (
    JSON_10K_BODY,
    JSON_BODY,
    SERVICE_WRITES,
    status_for,
    stream_items,
)
from bench.services import request_resources

serializers = build_serializers(serializer_module)


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
        return Response(await serializers.author(rows, many=True).adata)


class ArticlesView(APIView):
    async def get(self, request):
        page = await article_page_async()
        return Response(
            {
                "total": page["total"],
                "items": await serializers.article(page["items"], many=True).adata,
            }
        )

    async def post(self, request):
        serializer = serializers.create(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        try:
            article = await sync_to_async(create_article)(serializer.validated_data)
        except InvalidReferences as exc:
            raise ValidationError(str(exc)) from exc
        return Response(await serializers.article(article).adata, status=201)


class ArticleView(APIView):
    async def get(self, request, pk):
        try:
            article = await article_queryset().aget(pk=pk)
        except Article.DoesNotExist as exc:
            raise Http404 from exc
        return Response(await serializers.article(article).adata)


async def _rendered(items):
    renderer = JSONRenderer()
    for item in items:
        yield renderer.render(item) + b"\n"


class StreamView(APIView):
    async def get(self, request, scenario):
        return StreamingHttpResponse(
            _rendered(stream_items(scenario)), content_type="application/x-ndjson"
        )


class Authenticated:
    # adrf runs DRF's synchronous authentication in a worker thread.
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
                "user": await serializers.user(request.user).adata,
                "article": await serializers.article(article).adata,
            }
        )


class AuthenticatedArticlesView(Authenticated, generics.ListAPIView):
    serializer_class = serializers.article
    pagination_class = ArticlePagination

    def get_queryset(self):
        return article_queryset()

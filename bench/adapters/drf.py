"""Django REST framework: synchronous APIViews, served by the WSGI servers."""

from django.conf import settings
from django.http import Http404, StreamingHttpResponse
from rest_framework import generics
from rest_framework import serializers as serializer_module
from rest_framework.exceptions import MethodNotAllowed, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from bench.adapters.drf_common import ArticlePagination, JWTAuthentication
from bench.adapters.serializers import build_serializers
from bench.app.models import Article, Author
from bench.domain import (
    InvalidReferences,
    article_page,
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

ListAPIView = generics.ListAPIView
if settings.PROFILE == "drf-fastdrf":
    from fastdrf import serializers as serializer_module
    from fastdrf.response import DataResponse as Response
    from fastdrf.views import DispatchOptimizationMixin

    class APIView(DispatchOptimizationMixin, APIView):
        pass

    class ListAPIView(DispatchOptimizationMixin, generics.ListAPIView):
        pass


serializers = build_serializers(serializer_module)


class ServiceView(APIView):
    def get(self, request, scenario):
        if scenario in SERVICE_WRITES:
            raise MethodNotAllowed("GET")
        return Response(request_resources(request).execute(scenario))

    def post(self, request, scenario):
        if scenario not in SERVICE_WRITES:
            raise MethodNotAllowed("POST")
        return Response(
            request_resources(request).execute(scenario), status=status_for(scenario)
        )


class JsonView(APIView):
    def get(self, request):
        return Response(JSON_BODY)


class LargeJsonView(APIView):
    def get(self, request):
        return Response(JSON_10K_BODY)


class DatabaseView(APIView):
    def get(self, request):
        return Response(
            serializers.author(list(Author.objects.order_by("id")[:10]), many=True).data
        )


class ArticlesView(APIView):
    def get(self, request):
        page = article_page()
        return Response(
            {
                "total": page["total"],
                "items": serializers.article(page["items"], many=True).data,
            }
        )

    def post(self, request):
        serializer = serializers.create(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            article = create_article(serializer.validated_data)
        except InvalidReferences as exc:
            raise ValidationError(str(exc)) from exc
        return Response(serializers.article(article).data, status=201)


class ArticleView(APIView):
    def get(self, request, pk):
        try:
            article = article_queryset().get(pk=pk)
        except Article.DoesNotExist as exc:
            raise Http404 from exc
        return Response(serializers.article(article).data)


class StreamView(APIView):
    def get(self, request, scenario):
        renderer = JSONRenderer()
        lines = (renderer.render(item) + b"\n" for item in stream_items(scenario))
        return StreamingHttpResponse(lines, content_type="application/x-ndjson")


class Authenticated:
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]


class AuthenticatedArticleView(Authenticated, APIView):
    def get(self, request, pk):
        try:
            article = article_queryset().get(pk=pk)
        except Article.DoesNotExist as exc:
            raise Http404 from exc
        return Response(
            {
                "user": serializers.user(request.user).data,
                "article": serializers.article(article).data,
            }
        )


class AuthenticatedArticlesView(Authenticated, ListAPIView):
    serializer_class = serializers.article
    pagination_class = ArticlePagination

    def get_queryset(self):
        return article_queryset()

"""The same declared serializers for the DRF, adrf and aiodrf adapters."""

from typing import NamedTuple

from django.contrib.auth.models import User
from rest_framework import serializers as fields

from bench.app.models import Article, Author, Tag


class SerializerSet(NamedTuple):
    author: type
    article: type
    create: type
    user: type


def build_serializers(module) -> SerializerSet:
    class AuthorSerializer(module.ModelSerializer):
        class Meta:
            model = Author
            fields = ("id", "name")

    class TagSerializer(module.ModelSerializer):
        class Meta:
            model = Tag
            fields = ("id", "name")

    class ArticleSerializer(module.ModelSerializer):
        author = AuthorSerializer(read_only=True)
        tags = TagSerializer(many=True, read_only=True)

        class Meta:
            model = Article
            fields = ("id", "title", "body", "author", "tags")

    class CreateSerializer(module.Serializer):
        title = fields.CharField(max_length=120, trim_whitespace=False)
        body = fields.CharField(trim_whitespace=False)
        author_id = fields.IntegerField(min_value=1)
        tag_ids = fields.ListField(child=fields.IntegerField(min_value=1), max_length=5)

    class UserSerializer(module.ModelSerializer):
        class Meta:
            model = User
            fields = ("id", "username")

    return SerializerSet(
        AuthorSerializer, ArticleSerializer, CreateSerializer, UserSerializer
    )

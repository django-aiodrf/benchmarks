"""Shared query shape and atomic write contract, without shared serialization."""

from django.db import transaction

from bench.app.models import Article, Author, Tag


class InvalidReferences(ValueError):
    """The submitted author or tags are not present in the benchmark dataset."""


def article_queryset():
    # Stable ordering is part of the response contract, including related tags.
    from django.db.models import Prefetch

    return (
        Article.objects.select_related("author")
        .prefetch_related(Prefetch("tags", queryset=Tag.objects.order_by("id")))
        .order_by("id")
    )


def article_page():
    return {"total": Article.objects.count(), "items": list(article_queryset()[:20])}


async def article_page_async():
    return {
        "total": await Article.objects.acount(),
        "items": [item async for item in article_queryset()[:20]],
    }


def create_article(values):
    """Keep transaction boundaries and post-save relation reads identical."""
    with transaction.atomic():
        try:
            author = Author.objects.get(pk=values["author_id"])
        except Author.DoesNotExist as exc:
            raise InvalidReferences("Unknown author") from exc
        identifiers = set(values["tag_ids"])
        tags = list(Tag.objects.filter(pk__in=identifiers).order_by("id"))
        if len(tags) != len(identifiers):
            raise InvalidReferences("Unknown tag")
        article = Article.objects.create(
            title=values["title"], body=values["body"], author=author
        )
        article.tags.set(tags)
        return article_queryset().get(pk=article.pk)


def article_values(article):
    """Manual projection for plain Django and typed-schema adapters."""
    return {
        "id": article.id,
        "title": article.title,
        "body": article.body,
        "author": {"id": article.author_id, "name": article.author.name},
        "tags": [{"id": tag.id, "name": tag.name} for tag in article.tags.all()],
    }

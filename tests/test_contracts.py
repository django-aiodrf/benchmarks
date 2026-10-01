"""Query budgets, deterministic fixtures and atomic create behavior."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from bench.adapters.django import validate_input
from bench.app.models import Article
from bench.domain import (
    InvalidReferences,
    article_page,
    article_queryset,
    article_values,
    create_article,
)
from bench.fixtures import fixture_article
from bench.profiles import CREATE_BODY


@pytest.fixture
def dataset(db, settings):
    settings.BENCH_DATASET_SIZE = 20
    call_command("seed_benchmark", verbosity=0)


def test_list_and_detail_query_budgets(dataset, django_assert_num_queries):
    with django_assert_num_queries(3):
        page = article_page()
        values = [article_values(article) for article in page["items"]]
    assert page["total"] == 20
    assert values == [fixture_article(i) for i in range(1, 21)]
    with django_assert_num_queries(2):
        assert article_values(article_queryset().get(pk=1)) == fixture_article(1)


def test_create_persists_and_preloads_relations(dataset, django_assert_num_queries):
    article = create_article(CREATE_BODY)
    assert Article.objects.count() == 21
    assert article.pk > 20
    with django_assert_num_queries(0):
        value = article_values(article)
    assert value["title"] == CREATE_BODY["title"]
    assert [tag["id"] for tag in value["tags"]] == [1, 2]


@pytest.mark.parametrize("invalid", [{"author_id": 999}, {"tag_ids": [999]}])
def test_invalid_references_do_not_write(dataset, invalid):
    with pytest.raises(InvalidReferences):
        create_article({**CREATE_BODY, **invalid})
    assert Article.objects.count() == 20


@pytest.mark.django_db(transaction=True)
def test_seed_requires_reset_authorization(dataset, settings):
    # TRUNCATE must run after the seed transaction commits its deferred triggers.
    with pytest.raises(CommandError, match="Dataset exists"):
        call_command("seed_benchmark")
    with pytest.raises(CommandError, match="BENCH_ALLOW_RESET"):
        call_command("seed_benchmark", reset=True)
    settings.BENCH_ALLOW_RESET = True
    call_command("seed_benchmark", reset=True)
    assert Article.objects.count() == 20


@pytest.mark.parametrize("body", [b"null", b"[]", b"{}", b'{"title":""}', b"invalid"])
def test_plain_django_rejects_invalid_input(body):
    with pytest.raises(ValueError):
        validate_input(body)

"""The service workloads through Django's integrations: models, Documents and the cache API."""

from uuid import uuid4

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.db.models import Count, Sum

from bench.app.documents import SearchDocument
from bench.app.documents import WriteDocument as SearchWriteDocument
from bench.app.models import Author, SearchRecord
from bench.domain import article_page, article_page_async, article_values
from bench.mongoapp.models import Document, WriteDocument
from bench.services import CACHE_BATCH, CACHE_ITEM


def mongo_read():
    return (
        Document.objects.using("mongodb")
        .filter(category=3)
        .order_by("identifier")
        .values("identifier", "title", "category", "value")[:20]
    )


def mongo_aggregate():
    return (
        Document.objects.using("mongodb")
        .values("category")
        .annotate(count=Count("pk"), total=Sum("value"))
        .order_by("category")
    )


def search_query(search, name):
    # Served from the shard request cache after the first request: the
    # workload measures the client and the framework, not query execution.
    search = search.params(request_cache=True)
    if name == "elasticsearch-search":
        return (
            search.filter("term", category=3)
            .sort("id")[:20]
            .extra(track_total_hits=False)
        )
    search = search[:0]
    search.aggs.bucket(
        "categories", "terms", field="category", size=10, order={"_key": "asc"}
    ).metric("total", "sum", field="value")
    return search


def search_payload(data: dict, name: str) -> dict:
    if data.get("timed_out") or data.get("_shards", {}).get("failed", 0):
        raise ValueError("Elasticsearch returned a partial result")
    if name == "elasticsearch-search":
        return {"items": [hit["_source"] for hit in data["hits"]["hits"]]}
    return {
        "groups": [
            {
                "category": row["key"],
                "count": row["doc_count"],
                "total": int(row["total"]["value"]),
            }
            for row in data["aggregations"]["categories"]["buckets"]
        ]
    }


def write_record() -> SearchRecord:
    return SearchRecord(
        pk=uuid4().hex, identifier=0, title="Written document", category=0, value=7
    )


def database_payload(name: str) -> dict:
    if name == "db":
        return {"items": list(Author.objects.order_by("id").values("id", "name")[:10])}
    page = article_page()
    return {
        "total": page["total"],
        "items": [article_values(row) for row in page["items"]],
    }


def cached_database(name: str) -> dict:
    source, _, policy = name.split("-")
    key = "sql:" + source
    if policy == "miss":
        # A unique key guarantees a miss under concurrency without delete/get races.
        key += ":miss:" + uuid4().hex
    value = cache.get(key)
    if value is None:
        if policy == "hit":
            raise RuntimeError("Cache-hit fixture is missing; prepare it before timing")
        value = database_payload(source)
        cache.set(key, value, timeout=30)
    return value


def execute(name: str) -> dict:
    if name == "mongo-read":
        rows = mongo_read()
        return {"items": [{"id": row.pop("identifier"), **row} for row in rows]}
    if name == "mongo-aggregate":
        rows = mongo_aggregate()
        return {"groups": list(rows)}
    if name == "mongo-write":
        WriteDocument.objects.using("mongodb").create(title="Written document", value=7)
        return {"created": True}
    if name == "elasticsearch-write":
        record = write_record()
        count, errors = SearchWriteDocument().update(
            record, refresh=False, action="create"
        )
        if count != 1 or errors:
            raise ValueError("Elasticsearch write was not acknowledged")
        return {"created": True}
    if name.startswith("elasticsearch-"):
        search = search_query(SearchDocument.search(), name)
        return search_payload(search.execute().to_dict(), name)
    if name.startswith(("db-cache-", "articles-cache-")):
        return cached_database(name)
    if name == "cache-read":
        value = cache.get("read:item")
        if value is None:
            raise RuntimeError("Cache read fixture missing")
        return value
    if name == "cache-read-many":
        values = cache.get_many(CACHE_BATCH)
        return {"items": [values[key] for key in CACHE_BATCH]}
    if name == "cache-write":
        cache.set("write:item", CACHE_ITEM, timeout=300)
        return {"written": 1}
    if name == "cache-pipeline":
        # django-valkey implements set_many as a pipeline of TTL-bearing SETs.
        cache.set_many(
            {"write:" + key: value for key, value in CACHE_BATCH.items()}, timeout=300
        )
        return {"written": 3}
    raise ValueError(f"Unknown service workload: {name}")


async def aexecute(name: str, cache, elastic) -> dict:
    """Use native cache/search clients and Django's awaitable ORM API."""
    if name == "mongo-read":
        return {
            "items": [
                {"id": row.pop("identifier"), **row} async for row in mongo_read()
            ]
        }
    if name == "mongo-aggregate":
        return {"groups": [row async for row in mongo_aggregate()]}
    if name == "mongo-write":
        await WriteDocument.objects.using("mongodb").acreate(
            title="Written document", value=7
        )
        return {"created": True}
    if name.startswith("elasticsearch-"):
        # django-elasticsearch-dsl's Document API is synchronous. The separately
        # named native cases measure AsyncSearch/async_bulk without this bridge.
        return await sync_to_async(execute, thread_sensitive=False)(name)
    if name.startswith(("db-cache-", "articles-cache-")):
        source, _, policy = name.split("-")
        key = "sql:" + source
        if policy == "miss":
            key += ":miss:" + uuid4().hex
        value = await cache.aget(key)
        if value is None:
            if policy == "hit":
                raise RuntimeError(
                    "Cache-hit fixture is missing; prepare it before timing"
                )
            if source == "db":
                value = {
                    "items": [
                        row
                        async for row in Author.objects.order_by("id").values(
                            "id", "name"
                        )[:10]
                    ]
                }
            else:
                page = await article_page_async()
                value = {
                    "total": page["total"],
                    "items": [article_values(row) for row in page["items"]],
                }
            await cache.aset(key, value, timeout=30)
        return value
    if name == "cache-read":
        value = await cache.aget("read:item")
        if value is None:
            raise RuntimeError("Cache read fixture missing")
        return value
    if name == "cache-read-many":
        values = await cache.aget_many(CACHE_BATCH)
        return {"items": [values[key] for key in CACHE_BATCH]}
    if name == "cache-write":
        await cache.aset("write:item", CACHE_ITEM, timeout=300)
        return {"written": 1}
    if name == "cache-pipeline":
        await cache.aset_many(
            {"write:" + key: value for key, value in CACHE_BATCH.items()}, timeout=300
        )
        return {"written": 3}
    raise ValueError(f"Unknown service workload: {name}")

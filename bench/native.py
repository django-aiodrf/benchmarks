"""Direct-driver counterparts to the Django integration workloads.

Async adapters use AsyncMongoClient and AsyncSearch/async_bulk. Synchronous
adapters use the equivalent synchronous clients, never a per-request event loop.
"""

from uuid import uuid4

from elasticsearch.dsl import AsyncSearch, Document, Integer, Keyword, Long, Search
from elasticsearch.dsl.connections import connections
from elasticsearch.helpers import async_bulk, bulk

from bench.profiles import integration_scenario
from bench.services import DATABASE, INDEX, WRITE_INDEX
from bench.workloads import search_payload, search_query


class NativeDocument(Document):
    id = Integer()
    title = Keyword()
    category = Integer()
    value = Long()


def write_action() -> dict:
    # Same fields, UUID generation and one bulk operation as the Django path.
    document = NativeDocument(id=0, title="Written document", category=0, value=7)
    return {
        "_op_type": "create",
        "_index": WRITE_INDEX,
        "_id": uuid4().hex,
        "_source": document.to_dict(),
    }


def aggregation() -> list[dict]:
    return [
        {
            "$group": {
                "_id": "$category",
                "count": {"$sum": 1},
                "total": {"$sum": "$value"},
            }
        },
        {"$sort": {"_id": 1}},
        {"$project": {"_id": 0, "category": "$_id", "count": 1, "total": 1}},
    ]


def find(collection):
    return (
        collection.find(
            {"category": 3},
            {"_id": 0, "identifier": 1, "title": 1, "category": 1, "value": 1},
        )
        .sort("identifier", 1)
        .limit(20)
    )


def document_payload(row: dict) -> dict:
    """Convert BSON integer subclasses to the endpoint's JSON integer contract."""
    return {
        "id": int(row["identifier"]),
        "title": row["title"],
        "category": int(row["category"]),
        "value": int(row["value"]),
    }


def group_payload(row: dict) -> dict:
    return {name: int(row[name]) for name in ("category", "count", "total")}


def execute(name: str, mongo) -> dict:
    name = integration_scenario(name)
    if name == "mongo-read":
        with find(mongo[DATABASE]["documents"]) as cursor:
            return {"items": [document_payload(row) for row in cursor]}
    if name == "mongo-aggregate":
        with mongo[DATABASE]["documents"].aggregate(aggregation()) as cursor:
            return {"groups": [group_payload(row) for row in cursor]}
    if name == "mongo-write":
        result = mongo[DATABASE]["writes"].insert_one(
            {"title": "Written document", "value": 7}
        )
        if not result.acknowledged:
            raise ValueError("MongoDB write was not acknowledged")
        return {"created": True}
    if name == "elasticsearch-write":
        count, errors = bulk(
            connections.get_connection(), [write_action()], refresh=False
        )
        if count != 1 or errors:
            raise ValueError("Elasticsearch write was not acknowledged")
        return {"created": True}
    search = search_query(Search(using=connections.get_connection(), index=INDEX), name)
    return search_payload(search.execute().to_dict(), name)


async def aexecute(name: str, mongo, elastic) -> dict:
    name = integration_scenario(name)
    if name == "mongo-read":
        async with find(mongo[DATABASE]["documents"]) as cursor:
            return {"items": [document_payload(row) async for row in cursor]}
    if name == "mongo-aggregate":
        async with await mongo[DATABASE]["documents"].aggregate(
            aggregation()
        ) as cursor:
            return {"groups": [group_payload(row) async for row in cursor]}
    if name == "mongo-write":
        result = await mongo[DATABASE]["writes"].insert_one(
            {"title": "Written document", "value": 7}
        )
        if not result.acknowledged:
            raise ValueError("MongoDB write was not acknowledged")
        return {"created": True}
    if name == "elasticsearch-write":
        count, errors = await async_bulk(elastic, [write_action()], refresh=False)
        if count != 1 or errors:
            raise ValueError("Elasticsearch write was not acknowledged")
        return {"created": True}
    search = search_query(AsyncSearch(using=elastic, index=INDEX), name)
    return search_payload((await search.execute()).to_dict(), name)

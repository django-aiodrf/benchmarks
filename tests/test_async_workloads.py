"""Native clients are awaited; MongoDB uses Django's async QuerySet interface."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import msgspec
import pytest
from bson.int64 import Int64

from bench import native, workloads


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("scenario", ["mongo-native-read", "mongo-native-aggregate"])
def test_native_mongo_returns_json_types_and_closes_cursor(asynchronous, scenario):
    row = (
        {
            "identifier": Int64(3),
            "title": "Document 3",
            "category": Int64(3),
            "value": Int64(9),
        }
        if scenario.endswith("read")
        else {"category": Int64(3), "count": Int64(1), "total": Int64(9)}
    )
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.__enter__.return_value = cursor
    cursor.__aenter__.return_value = cursor
    cursor.__iter__.return_value = iter([row])
    cursor.__aiter__.return_value = [row]
    mongo = MagicMock()
    collection = mongo.__getitem__.return_value.__getitem__.return_value
    collection.find.return_value = cursor
    collection.aggregate = (
        AsyncMock(return_value=cursor)
        if asynchronous
        else MagicMock(return_value=cursor)
    )
    result = (
        asyncio.run(native.aexecute(scenario, mongo, None))
        if asynchronous
        else native.execute(scenario, mongo)
    )
    # BSON integer subclasses are not supported by every JSON encoder.
    assert msgspec.json.decode(msgspec.json.encode(result)) == result
    if asynchronous:
        cursor.__aexit__.assert_awaited_once()
    else:
        cursor.__exit__.assert_called_once()


def test_async_mongo_write_uses_acreate(monkeypatch):
    rows = MagicMock()
    rows.acreate = AsyncMock()
    monkeypatch.setattr(workloads.WriteDocument.objects, "using", lambda alias: rows)
    result = asyncio.run(workloads.aexecute("mongo-write", None, None))
    assert result == {"created": True}
    rows.acreate.assert_awaited_once_with(title="Written document", value=7)
    rows.create.assert_not_called()


def test_async_native_cursor_closes_when_cancelled():
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.__aenter__.return_value = cursor
    cursor.__aiter__.side_effect = asyncio.CancelledError
    mongo = MagicMock()
    mongo.__getitem__.return_value.__getitem__.return_value.find.return_value = cursor
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(native.aexecute("mongo-native-read", mongo, None))
    cursor.__aexit__.assert_awaited_once()
    assert cursor.__aexit__.await_args.args[0] is asyncio.CancelledError


def test_async_cache_pipeline_awaits_backend(monkeypatch):
    backend = MagicMock()
    backend.aset_many = AsyncMock()
    monkeypatch.setattr(workloads, "execute", MagicMock(side_effect=AssertionError))
    result = asyncio.run(workloads.aexecute("cache-pipeline", backend, None))
    assert result == {"written": 3}
    backend.aset_many.assert_awaited_once()
    backend.set_many.assert_not_called()


def test_async_search_uses_async_dsl(monkeypatch):
    search = MagicMock()
    search.params.return_value = search
    search.filter.return_value = search
    search.sort.return_value = search
    search.__getitem__.return_value = search
    search.extra.return_value = search
    response = MagicMock()
    response.to_dict.return_value = {"hits": {"hits": [{"_source": {"id": 3}}]}}
    search.execute = AsyncMock(return_value=response)
    monkeypatch.setattr(native, "AsyncSearch", lambda **kwargs: search)
    result = asyncio.run(native.aexecute("elasticsearch-native-search", None, object()))
    assert result == {"items": [{"id": 3}]}
    search.execute.assert_awaited_once()

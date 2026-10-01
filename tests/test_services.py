"""Network-free checks of clients, workload equivalence and failure handling."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from unittest.mock import MagicMock

import httpx
import pytest

from bench.services import (
    Resources,
    ServiceConfig,
    cpu_work,
    documents,
    expected_service,
    resources,
)


def test_deterministic_fixture_and_cpu_inputs():
    assert documents(20) == documents(20)
    assert cpu_work(10) == sum(i * i + 17 for i in range(10))
    config = ServiceConfig(cpu_rounds=10)
    assert expected_service("mixed-inline", 20, config) == expected_service(
        "mixed-thread", 20, config
    )
    assert len(expected_service("mongo-read", 1000, config)["items"]) == 20
    assert (
        sum(
            row["count"]
            for row in expected_service("mongo-aggregate", 20, config)["groups"]
        )
        == config.documents
    )


def test_django_elasticsearch_document_preserves_all_model_fields():
    from bench.app.documents import SearchDocument, WriteDocument
    from bench.app.models import SearchRecord

    model = SearchRecord(pk="3", identifier=3, title="Document 3", category=3, value=9)
    expected = {"id": 3, "title": "Document 3", "category": 3, "value": 9}
    for document in (SearchDocument, WriteDocument):
        assert document().prepare(model) == expected


def test_elastic_partial_result_is_an_error(monkeypatch):
    from bench import workloads

    search = MagicMock()
    search.params.return_value = search
    search.filter.return_value = search
    search.sort.return_value = search
    search.__getitem__.return_value = search
    search.extra.return_value = search
    search.execute.return_value.to_dict.return_value = {"timed_out": True}
    monkeypatch.setattr(workloads.SearchDocument, "search", lambda: search)
    with pytest.raises(ValueError, match="partial"):
        workloads.execute("elasticsearch-search")


def test_sync_and_async_clients_use_the_same_workload_contracts():
    async def run():
        config = ServiceConfig(cpu_rounds=10)

        def handler(request):
            identifier = int(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"id": identifier, "value": identifier * 7})

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as sync_http,
            ThreadPoolExecutor(max_workers=3) as io,
            ThreadPoolExecutor(max_workers=1) as cpu,
        ):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as async_http:
                clients = Resources(config, sync_http, io, cpu, async_http=async_http)
                for name in (
                    "http-single",
                    "http-fanout",
                    "mixed-inline",
                    "mixed-thread",
                ):
                    assert clients.execute(name) == expected_service(name, 20, config)
                    assert await clients.aexecute(name) == expected_service(
                        name, 20, config
                    )

    asyncio.run(run())


def test_mongo_queries_use_django_backend_alias(monkeypatch):
    from bench import workloads

    queryset = MagicMock()
    queryset.filter.return_value.order_by.return_value.values.return_value.__getitem__.return_value = [
        {"identifier": 3, "title": "Document 3", "category": 3, "value": 9}
    ]
    using = MagicMock(return_value=queryset)
    monkeypatch.setattr(workloads.Document.objects, "using", using)
    assert workloads.execute("mongo-read") == {
        "items": [{"id": 3, "title": "Document 3", "category": 3, "value": 9}]
    }
    using.assert_called_once_with("mongodb")
    queryset.filter.assert_called_once_with(category=3)


def test_cache_misses_are_unique_and_hits_never_query(monkeypatch):
    from bench import workloads

    backend = MagicMock()
    backend.get.return_value = None
    monkeypatch.setattr(workloads, "cache", backend)
    payload = MagicMock(return_value={"items": []})
    monkeypatch.setattr(workloads, "database_payload", payload)
    with pytest.raises(RuntimeError, match="Cache-hit"):
        workloads.execute("db-cache-hit")
    payload.assert_not_called()
    workloads.execute("db-cache-miss")
    workloads.execute("db-cache-miss")
    first, second = backend.set.call_args_list
    assert first.args[0] != second.args[0]
    assert first.kwargs["timeout"] == 30


def test_pipeline_uses_public_cache_api(monkeypatch):
    from bench import workloads

    backend = MagicMock()
    monkeypatch.setattr(workloads, "cache", backend)
    assert workloads.execute("cache-pipeline") == {"written": 3}
    backend.set_many.assert_called_once()
    assert len(backend.set_many.call_args.args[0]) == 3
    assert backend.set_many.call_args.kwargs == {"timeout": 300}


@pytest.mark.parametrize("fail", [False, True])
def test_worker_resources_close_on_exit(fail):
    async def run():
        with pytest.raises(ValueError) if fail else nullcontext():
            async with resources() as clients:
                assert not clients.http.is_closed
                assert not clients.async_http.is_closed
                if fail:
                    raise ValueError("Simulated worker failure")
        assert clients.http.is_closed
        assert clients.async_http.is_closed
        for executor in (clients.io_executor, clients.cpu_executor):
            with pytest.raises(RuntimeError, match="shutdown"):
                executor.submit(lambda: None)

    asyncio.run(run())


def test_lifespan_restarts_with_a_new_elasticsearch_client():
    from elasticsearch.dsl.connections import connections

    async def run():
        async with resources():
            first = connections.get_connection()
        async with resources():
            assert connections.get_connection() is not first

    asyncio.run(run())


def test_mongo_write_verification_rejects_missing_or_wrong_documents(monkeypatch):
    from bench.infrastructure import verify_writes
    from bench.mongoapp.models import WriteDocument

    rows = MagicMock()
    monkeypatch.setattr(WriteDocument.objects, "using", lambda alias: rows)
    rows.count.return_value = 2
    with pytest.raises(ValueError, match="count/content"):
        verify_writes("mongo-write", 3)
    rows.count.return_value = 3
    rows.filter.return_value.count.return_value = 2
    with pytest.raises(ValueError, match="count/content"):
        verify_writes("mongo-write", 3)
    rows.filter.return_value.count.return_value = 3
    verify_writes("mongo-write", 3)


def test_service_warmup_runs_each_mongodb_and_elasticsearch_workload(monkeypatch):
    import bench.native
    import bench.workloads
    from bench.infrastructure import warm_services

    calls = []
    monkeypatch.setattr(bench.workloads, "execute", lambda name: calls.append(name))
    monkeypatch.setattr(bench.native, "execute", lambda name, mongo: calls.append(name))

    warmed = warm_services(
        ["json", "db", "mongo-aggregate", "elasticsearch-native-search"],
        requests=10,
        threads=3,
    )

    assert warmed == {"mongo-aggregate": 10, "elasticsearch-native-search": 10}
    assert (
        sorted(calls) == ["elasticsearch-native-search"] * 10 + ["mongo-aggregate"] * 10
    )


def test_a_connection_per_request_is_closed_by_the_http_fixture(monkeypatch):
    # The client closing first would leave each connection's port in
    # TIME_WAIT for a minute: thousands per sample exhaust the ephemeral
    # ports. With `Connection: close` the fixture closes first instead.
    import bench.services as services

    seen = []

    def handler(request):
        seen.append(request.headers.get("connection"))
        return httpx.Response(200, json={"id": 1})

    transport = httpx.MockTransport(handler)
    client, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(
        services.httpx, "Client", lambda **kw: client(transport=transport, **kw)
    )
    monkeypatch.setattr(
        services.httpx,
        "AsyncClient",
        lambda **kw: async_client(transport=transport, **kw),
    )
    config = ServiceConfig(cpu_rounds=10)
    with ThreadPoolExecutor(1) as io, ThreadPoolExecutor(1) as cpu:
        clients = Resources(config, None, io, cpu, async_http=None)
        assert clients.execute("http-no-reuse") == {"items": [{"id": 1}]}
        assert asyncio.run(clients.aexecute("http-no-reuse")) == {"items": [{"id": 1}]}
    assert seen == ["close", "close"]

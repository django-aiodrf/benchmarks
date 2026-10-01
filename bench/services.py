"""Worker-owned HTTP clients and common service response contracts."""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass

import httpx
from pymongo import AsyncMongoClient, MongoClient

from bench.profiles import integration_scenario

DATABASE = "aiodrf_benchmarks"
COLLECTION = "documents"
INDEX = "aiodrf-benchmarks-documents"
WRITE_INDEX = "aiodrf-benchmarks-writes"
CACHE_ITEM = {"id": 1, "title": "Cached document", "value": 7}
CACHE_BATCH = {f"batch:{i}": {"id": i, "value": i * 7} for i in range(1, 4)}


@dataclass(frozen=True)
class ServiceConfig:
    mongo_url: str = "mongodb://127.0.0.1:57027"
    elastic_url: str = "http://127.0.0.1:59200"
    http_url: str = "http://127.0.0.1:58090"
    cpu_rounds: int = 50_000
    cpu_threads: int = 4
    pool_size: int = 64
    # Documents in MongoDB and Elasticsearch: enough for the reads' twenty
    # results, few enough that the aggregations do not limit throughput.
    documents: int = 200

    @classmethod
    def from_env(cls):
        result = cls(
            mongo_url=os.environ.get(
                "BENCH_MONGO_URL",
                "mongodb://127.0.0.1:" + os.environ.get("BENCH_MONGO_PORT", "57027"),
            ),
            elastic_url=os.environ.get(
                "BENCH_ES_URL",
                "http://127.0.0.1:" + os.environ.get("BENCH_ES_PORT", "59200"),
            ),
            http_url=os.environ.get(
                "BENCH_HTTP_URL",
                "http://127.0.0.1:" + os.environ.get("BENCH_HTTP_PORT", "58090"),
            ),
            cpu_rounds=int(os.environ.get("BENCH_CPU_ROUNDS", "50000")),
            cpu_threads=int(os.environ.get("BENCH_CPU_THREADS", "4")),
            pool_size=int(os.environ.get("BENCH_CLIENT_POOL", "64")),
            documents=int(os.environ.get("BENCH_DOCUMENTS", "200")),
        )
        if (
            not 1 <= result.cpu_rounds <= 10_000_000
            or not 1 <= result.cpu_threads <= 64
            or not 1 <= result.pool_size <= 256
            or not 20 <= result.documents <= 100_000
        ):
            raise ValueError(
                "Invalid CPU rounds, CPU threads, client pool size or document count"
            )
        return result


def documents(size: int) -> list[dict]:
    return [
        {"id": i, "title": f"Document {i}", "category": i % 10, "value": i * 3}
        for i in range(1, size + 1)
    ]


def expected_service(name: str, size: int, config: ServiceConfig) -> dict:
    name = integration_scenario(name)
    if name in ("mongo-read", "elasticsearch-search"):
        return {
            "items": [
                item for item in documents(config.documents) if item["category"] == 3
            ][:20]
        }
    if name in ("mongo-aggregate", "elasticsearch-aggregate"):
        rows = documents(config.documents)
        return {
            "groups": [
                {
                    "category": c,
                    "count": sum(row["category"] == c for row in rows),
                    "total": sum(row["value"] for row in rows if row["category"] == c),
                }
                for c in range(10)
            ]
        }
    if name in ("mongo-write", "elasticsearch-write"):
        return {"created": True}
    if name == "cache-read":
        return CACHE_ITEM
    if name == "cache-read-many":
        return {"items": list(CACHE_BATCH.values())}
    if name in ("cache-write", "cache-pipeline"):
        return {"written": 1 if name == "cache-write" else 3}
    if name.startswith(("db-cache-", "articles-cache-")):
        from bench.fixtures import author_values, fixture_article

        if name.startswith("db-"):
            return {"items": [author_values(i) for i in range(1, 11)]}
        return {"total": size, "items": [fixture_article(i) for i in range(1, 21)]}
    result = {
        "items": [
            {"id": i, "value": i * 7}
            for i in range(1, 2 if name in ("http-single", "http-no-reuse") else 4)
        ]
    }
    if name.startswith("mixed-"):
        result["checksum"] = cpu_work(config.cpu_rounds)
    return result


# http-no-reuse opens a connection per request. Asking the fixture to close it
# leaves the TIME_WAIT on the fixture's side: a client that closes first keeps
# each local port for a minute, and a few samples exhaust the ephemeral range.
CLOSE = {"Connection": "close"}


def cpu_work(rounds: int) -> int:
    """Pure Python arithmetic intentionally retains the GIL on standard CPython."""
    return sum((i * i + 17) % 65521 for i in range(rounds))


@dataclass
class SyncResources:
    config: ServiceConfig
    http: httpx.Client
    io_executor: ThreadPoolExecutor
    cpu_executor: ThreadPoolExecutor
    mongo: MongoClient | None = None

    def fetch(self, identifier: int) -> dict:
        response = self.http.get(f"{self.config.http_url}/items/{identifier}")
        response.raise_for_status()
        return response.json()

    def execute(self, name: str) -> dict:
        if "-native-" in name:
            from bench.native import execute

            return execute(name, self.mongo)
        if not name.startswith(("http-", "mixed-")):
            from bench.workloads import execute

            return execute(name)
        if name == "http-no-reuse":
            with httpx.Client(timeout=10, trust_env=False) as client:
                response = client.get(f"{self.config.http_url}/items/1", headers=CLOSE)
                response.raise_for_status()
                return {"items": [response.json()]}
        if name == "http-single":
            return {"items": [self.fetch(1)]}
        futures = [self.io_executor.submit(self.fetch, i) for i in range(1, 4)]
        result = {"items": [future.result() for future in futures]}
        if name == "mixed-inline":
            result["checksum"] = cpu_work(self.config.cpu_rounds)
        elif name == "mixed-thread":
            result["checksum"] = self.cpu_executor.submit(
                cpu_work, self.config.cpu_rounds
            ).result()
        return result


@dataclass(kw_only=True)
class Resources(SyncResources):
    async_http: httpx.AsyncClient
    async_cache: object = None
    async_elastic: object = None
    async_mongo: AsyncMongoClient | None = None

    async def afetch(self, identifier: int) -> dict:
        response = await self.async_http.get(
            f"{self.config.http_url}/items/{identifier}"
        )
        response.raise_for_status()
        return response.json()

    async def aexecute(self, name: str) -> dict:
        if "-native-" in name:
            from bench.native import aexecute

            return await aexecute(name, self.async_mongo, self.async_elastic)
        if not name.startswith(("http-", "mixed-")):
            from bench.workloads import aexecute

            return await aexecute(name, self.async_cache, self.async_elastic)
        if name == "http-no-reuse":
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(
                    f"{self.config.http_url}/items/1", headers=CLOSE
                )
                response.raise_for_status()
                return {"items": [response.json()]}
        if name == "http-single":
            return {"items": [await self.afetch(1)]}
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self.afetch(i)) for i in range(1, 4)]
        result = {"items": [task.result() for task in tasks]}
        if name == "mixed-inline":
            result["checksum"] = cpu_work(self.config.cpu_rounds)
        elif name == "mixed-thread":
            result["checksum"] = await asyncio.get_running_loop().run_in_executor(
                self.cpu_executor, cpu_work, self.config.cpu_rounds
            )
        return result


@asynccontextmanager
async def resources() -> AsyncGenerator[Resources]:
    """Create clients after worker start and close them before loop shutdown."""
    from django.conf import settings
    from django.core.cache import cache
    from django_valkey.async_cache.cache import AsyncValkeyCache
    from elasticsearch import AsyncElasticsearch
    from elasticsearch.dsl.connections import connections

    config = ServiceConfig.from_env()
    limits = httpx.Limits(
        max_connections=config.pool_size, max_keepalive_connections=config.pool_size
    )
    with ExitStack() as stack:
        async with AsyncExitStack() as async_stack:
            http = stack.enter_context(
                httpx.Client(limits=limits, timeout=10, trust_env=False)
            )
            async_http = await async_stack.enter_async_context(
                httpx.AsyncClient(limits=limits, timeout=10, trust_env=False)
            )
            # Initialize the DSL client once before requests can race its lazy setup.
            # remove_connection() also removes its configuration on shutdown.
            connections.configure(**settings.ELASTICSEARCH_DSL)
            elastic = connections.get_connection()
            # Remove the registered client after closing it so a later lifespan
            # in the same process cannot retrieve a closed transport.
            stack.callback(connections.remove_connection, "default")
            stack.callback(elastic.close)
            async_elastic = await async_stack.enter_async_context(
                AsyncElasticsearch(**settings.ELASTICSEARCH_DSL["default"])
            )
            mongo_options = settings.DATABASES["mongodb"]["OPTIONS"]
            mongo = stack.enter_context(
                MongoClient(config.mongo_url, connect=False, **mongo_options)
            )
            async_mongo = await async_stack.enter_async_context(
                AsyncMongoClient(config.mongo_url, connect=False, **mongo_options)
            )
            cache_config = settings.CACHES["async"]
            # Own the instance here, not in Django's request-local cache handler.
            # Native clients must be closed on the loop which used them.
            async_cache = AsyncValkeyCache(cache_config["LOCATION"], cache_config)
            async_stack.push_async_callback(async_cache.aclose)
            await async_cache.client.get_client()
            cache.client.get_client()
            stack.callback(cache.client.disconnect)
            io_executor = stack.enter_context(
                ThreadPoolExecutor(
                    max_workers=config.pool_size, thread_name_prefix="bench-io"
                )
            )
            cpu_executor = stack.enter_context(
                ThreadPoolExecutor(
                    max_workers=config.cpu_threads, thread_name_prefix="bench-cpu"
                )
            )
            yield Resources(
                config=config,
                http=http,
                async_http=async_http,
                io_executor=io_executor,
                cpu_executor=cpu_executor,
                async_cache=async_cache,
                async_elastic=async_elastic,
                mongo=mongo,
                async_mongo=async_mongo,
            )


@contextmanager
def sync_resources() -> Generator[SyncResources]:
    """WSGI owns synchronous clients only, initialized after worker creation."""
    from django.conf import settings
    from django.core.cache import cache
    from elasticsearch.dsl.connections import connections

    config = ServiceConfig.from_env()
    limits = httpx.Limits(
        max_connections=config.pool_size, max_keepalive_connections=config.pool_size
    )
    with ExitStack() as stack:
        http = stack.enter_context(
            httpx.Client(limits=limits, timeout=10, trust_env=False)
        )
        connections.configure(**settings.ELASTICSEARCH_DSL)
        elastic = connections.get_connection()
        stack.callback(connections.remove_connection, "default")
        stack.callback(elastic.close)
        mongo = stack.enter_context(
            MongoClient(
                config.mongo_url,
                connect=False,
                **settings.DATABASES["mongodb"]["OPTIONS"],
            )
        )
        cache.client.get_client()
        stack.callback(cache.client.disconnect)
        io = stack.enter_context(
            ThreadPoolExecutor(
                max_workers=config.pool_size, thread_name_prefix="bench-io"
            )
        )
        cpu = stack.enter_context(
            ThreadPoolExecutor(
                max_workers=config.cpu_threads, thread_name_prefix="bench-cpu"
            )
        )
        yield SyncResources(config, http, io, cpu, mongo)


def request_resources(request) -> SyncResources | Resources:
    django_request = getattr(request, "_request", request)
    if not hasattr(django_request, "scope"):
        return django_request.META["bench.resources"]
    return django_request.scope["state"]["resources"]

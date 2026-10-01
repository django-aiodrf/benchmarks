"""Seed and reset the services' benchmark data, and count what the writes stored."""

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import django
import httpx
from django.apps import apps

from bench.profiles import integration_scenario
from bench.services import (
    CACHE_BATCH,
    CACHE_ITEM,
    INDEX,
    WRITE_INDEX,
    ServiceConfig,
    documents,
)


def setup_django() -> None:
    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")
        django.setup()


def image_records() -> dict:
    """Inspect only this Compose project; remote services need not use Docker."""
    try:
        result = subprocess.run(
            ["docker", "compose", "-p", "aiodrf-benchmarks", "ps", "-a", "-q"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode or not result.stdout.strip():
            return {"available": False}
        containers = json.loads(
            subprocess.check_output(
                ["docker", "inspect", *result.stdout.split()], text=True, timeout=10
            )
        )
        images = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    *sorted({item["Image"] for item in containers}),
                ],
                text=True,
                timeout=10,
            )
        )
        return {
            "available": True,
            "containers": [
                {
                    "name": item["Name"],
                    "image_id": item["Image"],
                    "configured_image": item["Config"]["Image"],
                    "cpu_quota": item["HostConfig"]["NanoCpus"],
                    "memory_bytes": item["HostConfig"]["Memory"],
                }
                for item in containers
            ],
            "images": [
                {"id": item["Id"], "repo_digests": item.get("RepoDigests", [])}
                for item in images
            ],
        }
    except OSError, subprocess.SubprocessError:
        return {"available": False}


def prime_cache(*, sql: bool, reset: bool) -> dict:
    from django.conf import settings
    from django.core.cache import cache
    from valkey import Valkey

    from bench.workloads import database_payload

    # Administrative inspection/reset only. Timed operations use Django's API.
    with Valkey.from_url(
        settings.CACHES["default"]["LOCATION"], socket_timeout=5
    ) as client:
        prefix = cache.make_key("*")
        if reset:
            batch = []
            for key in client.scan_iter(match=prefix, count=1000):
                batch.append(key)
                if len(batch) == 1000:
                    client.delete(*batch)
                    batch.clear()
            if batch:
                client.delete(*batch)
        elif next(client.scan_iter(match=prefix, count=1), None) is not None:
            raise ValueError("Cache namespace exists; explicit reset is required")
        info = client.info("server")
        config = {
            name: client.config_get(name)[name]
            for name in (
                "maxmemory",
                "maxmemory-policy",
                "io-threads",
                "appendonly",
                "save",
                "tcp-backlog",
            )
        }
    cache.set("read:item", CACHE_ITEM, timeout=None)
    cache.set_many(CACHE_BATCH, timeout=None)
    if sql:
        for name in ("db", "articles"):
            cache.set("sql:" + name, database_payload(name), timeout=None)
    return {
        "version": info.get("valkey_version", info.get("redis_version")),
        "config": config,
        "backend": settings.CACHES["default"]["BACKEND"],
    }


def prepare_services(services: set[str], *, size: int, reset: bool) -> dict:
    setup_django()
    from django.core.management import call_command
    from django.db import connections
    from elasticsearch.dsl.connections import connections as elastic_connections

    from bench.app.documents import SearchDocument
    from bench.app.documents import WriteDocument as SearchWriteDocument
    from bench.app.models import SearchRecord
    from bench.mongoapp.models import Document, WriteDocument

    rows = documents(ServiceConfig.from_env().documents)
    record = {
        "dataset_sha256": hashlib.sha256(
            json.dumps(rows, sort_keys=True).encode()
        ).hexdigest()
    }
    if "mongodb" in services:
        call_command("migrate", database="mongodb", verbosity=0, interactive=False)
        queryset = Document.objects.using("mongodb")
        if (
            queryset.exists() or WriteDocument.objects.using("mongodb").exists()
        ) and not reset:
            raise ValueError("MongoDB dataset exists; explicit reset is required")
        queryset.all().delete()
        WriteDocument.objects.using("mongodb").all().delete()
        queryset.bulk_create(
            [
                Document(
                    identifier=row["id"],
                    title=row["title"],
                    category=row["category"],
                    value=row["value"],
                )
                for row in rows
            ]
        )
        connection = connections["mongodb"]
        record["mongodb"] = {
            "version": connection.connection.server_info()["version"],
            "count": queryset.count(),
            "indexes": connection.database["documents"].index_information(),
            "server_options": connection.connection.admin.command("getCmdLineOpts")[
                "parsed"
            ],
            "backend": "django_mongodb_backend",
        }
    if "elasticsearch" in services:
        client = elastic_connections.get_connection()
        for index, document in (
            (INDEX, SearchDocument),
            (WRITE_INDEX, SearchWriteDocument),
        ):
            if client.indices.exists(index=index):
                if not reset:
                    raise ValueError(
                        "Elasticsearch dataset exists; explicit reset is required"
                    )
                client.indices.delete(index=index)
            document.init()
        client.indices.put_settings(index=INDEX, settings={"refresh_interval": "-1"})
        count, errors = SearchDocument().update(
            [
                SearchRecord(
                    pk=str(row["id"]),
                    identifier=row["id"],
                    title=row["title"],
                    category=row["category"],
                    value=row["value"],
                )
                for row in rows
            ],
            refresh=False,
            chunk_size=1000,
        )
        if count != len(rows) or errors:
            raise ValueError("Elasticsearch fixture was not fully indexed")
        client.indices.put_settings(index=INDEX, settings={"refresh_interval": "1s"})
        client.indices.refresh(index=INDEX)
        record["elasticsearch"] = {
            "version": client.info()["version"]["number"],
            "index_settings": dict(
                client.indices.get_settings(index=f"{INDEX},{WRITE_INDEX}")
            ),
            "backend": "django_elasticsearch_dsl",
        }
    if "valkey" in services:
        record["valkey"] = prime_cache(sql="postgres" in services, reset=reset)
    if "http" in services:
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(ServiceConfig.from_env().http_url + "/health")
            response.raise_for_status()
            record["http"] = response.json()
            if record["http"].get("implementation") != "go-net-http":
                raise ValueError("HTTP workloads require the Go fixture")
    return record


# Requests per MongoDB and Elasticsearch workload before the first sample.
SERVICE_WARMUP = 20_000


def warm_services(
    scenarios, *, requests: int = SERVICE_WARMUP, threads: int = 16
) -> dict[str, int]:
    """Run each MongoDB and Elasticsearch workload before the first sample.

    Elasticsearch's JIT compiler needs far more requests than one sample's
    warmup: in a cold run, the first framework measured used up to twice the
    Elasticsearch CPU per request of those measured after it.
    """
    setup_django()
    from django.conf import settings
    from django.db import connections
    from pymongo import MongoClient

    from bench import native, workloads

    selected = [
        name for name in scenarios if name.startswith(("mongo-", "elasticsearch-"))
    ]
    if not selected:
        return {}

    def batch(name: str, mongo, count: int) -> None:
        try:
            for _ in range(count):
                if "-native-" in name:
                    native.execute(name, mongo)
                else:
                    workloads.execute(name)
        finally:
            connections.close_all()

    config = ServiceConfig.from_env()
    options = settings.DATABASES["mongodb"]["OPTIONS"]
    with (
        MongoClient(config.mongo_url, connect=False, **options) as mongo,
        ThreadPoolExecutor(threads, thread_name_prefix="bench-warmup") as executor,
    ):
        for name in selected:
            counts = [
                requests // threads + (i < requests % threads) for i in range(threads)
            ]
            for future in [executor.submit(batch, name, mongo, n) for n in counts]:
                future.result()
    return dict.fromkeys(selected, requests)


def prepare_case(scenario: str) -> None:
    """Reset write volume and cache state outside each measured interval."""
    setup_django()
    scenario = integration_scenario(scenario)
    if scenario == "mongo-write":
        from bench.mongoapp.models import WriteDocument

        WriteDocument.objects.using("mongodb").all().delete()
    elif scenario == "elasticsearch-write":
        from elasticsearch.dsl.connections import connections

        from bench.app.documents import WriteDocument

        client = connections.get_connection()
        client.indices.delete(index=WRITE_INDEX)
        WriteDocument.init()
    elif scenario.startswith(("cache-", "db-cache-", "articles-cache-")):
        prime_cache(sql=not scenario.startswith("cache-"), reset=True)


def verify_writes(scenario: str, expected: int) -> None:
    """Check storage state, not only the successful HTTP status."""
    setup_django()
    scenario = integration_scenario(scenario)
    if scenario == "mongo-write":
        from bench.mongoapp.models import WriteDocument

        rows = WriteDocument.objects.using("mongodb")
        if (
            rows.count() != expected
            or rows.filter(title="Written document", value=7).count() != expected
        ):
            raise ValueError(
                "MongoDB write count/content differs from acknowledged requests"
            )
    elif scenario == "elasticsearch-write":
        from elasticsearch.dsl.connections import connections

        from bench.app.documents import WriteDocument

        connections.get_connection().indices.refresh(index=WRITE_INDEX)
        search = WriteDocument.search()
        if (
            search.count() != expected
            or search.filter("term", title="Written document")
            .filter("term", value=7)
            .count()
            != expected
        ):
            raise ValueError(
                "Elasticsearch write count/content differs from acknowledged requests"
            )
    elif scenario.startswith("cache-"):
        from django.core.cache import cache

        if scenario == "cache-write" and cache.get("write:item") != CACHE_ITEM:
            raise ValueError("Cache write content differs")
        if scenario == "cache-pipeline":
            values = {"write:" + key: value for key, value in CACHE_BATCH.items()}
            if cache.get_many(values) != values:
                raise ValueError("Cache pipeline content differs")

# Infrastructure

`compose.yaml` runs the services the workloads use, as the Compose project
`aiodrf-benchmarks`. Their ports bind to loopback only, with local-only
credentials; do not expose them. Named volumes keep the data between
restarts; the runner resets only the benchmark's own tables, collections,
indexes and cache keys.

```sh
docker compose -p aiodrf-benchmarks up -d --wait
.venv/bin/aiodrf-bench prepare --reset-database
docker compose -p aiodrf-benchmarks down   # keeps the volumes
```

| Service | Image | CPUs | Memory | Port |
| --- | --- | --- | --- | --- |
| PostgreSQL | `postgres:18.6-bookworm` | 16–17 | 4 GiB | 55441 |
| Elasticsearch | `elasticsearch:9.5.4` | 18–21 | 4 GiB (2 GiB heap) | 59200 |
| MongoDB | `mongo:8.0.4` | 22–26 | 2 GiB | 57027 |
| Valkey | `valkey/valkey:9.0.6` | 27 | 2 GiB | 56379 |
| HTTP fixture | built from `infra/http` (Go) | 27 | 256 MiB | 58090 |
| PgBouncer (optional) | `cloudnative-pg/pgbouncer:1.25.2`, digest-pinned | 27 | 256 MiB | 56432 |

Each service is confined to its CPUs with a cpuset; the variables in
[methodology](methodology.md#cpu-layout) change them. The runner records the
CPU use of every service during every sample.

## Write durability

The write workloads measure how frameworks handle writes, so the services
acknowledge a write without waiting for a disk flush:

| Service | Setting | Effect |
| --- | --- | --- |
| PostgreSQL | `synchronous_commit=off` (`fsync=on`) | A commit returns before its WAL is flushed; a crash can lose the last commits, never corrupt the database |
| Elasticsearch | write index: `translog.durability: async`, `sync_interval: 5s`, `refresh_interval: -1` | Writes are acknowledged before the translog is synced, and not made searchable until the runner refreshes the index to count them |
| MongoDB | `w: 1`, `journal: false` | The primary acknowledges before its journal is flushed |

Reads are unaffected. The settings apply to every framework identically.

## PostgreSQL

`shared_buffers=512MB`, `effective_cache_size=3GB`, `work_mem=8MB`,
`max_connections=400`, `io_method=worker` with four I/O workers,
`random_page_cost=1.1`. Each worker process has a psycopg connection pool of
at most 10 connections (`CONN_MAX_AGE=0`, connections return to the pool
after each request). The dataset is 1,000 articles, 10 authors, 5 tags and
one user, seeded again before every sample that uses PostgreSQL.

## Elasticsearch

A single node without security, with one primary shard and no replica per
index. Searches and aggregations request the shard request cache
(`request_cache=true`): after the first request Elasticsearch answers from
it, and the workload measures the client and the framework rather than query
execution. The read index is seeded once with the same 200 documents as
MongoDB (`BENCH_DOCUMENTS`); the write index is recreated before each write
sample.

The Python client is pinned to 9.0.x (the server is 9.5.4):
django-elasticsearch-dsl 9.0 prepares empty documents with clients 9.1 and
later. `tests/test_services.py::test_django_elasticsearch_document_preserves_all_model_fields`
fails when that is the case; lift the pin once it passes with a newer client.

The host needs `vm.max_map_count` of at least 262144
([Elastic's Docker requirements](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-docker-prod)).

## MongoDB

A standalone server with a 0.5 GiB WiredTiger cache and an index on
`(category, identifier)` for the read filter and sort; the aggregation scans
all 200 documents. MongoDB 8.0.4 is used because 8.3 refuses to start on Linux 6.19
and later ([SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912));
on an older kernel a newer 8.x image can be selected in `compose.yaml`.

## Valkey

`maxmemory 1gb` with `allkeys-lru`, one I/O thread, no persistence. The cache
workloads use django-valkey's synchronous and native asynchronous backends;
the asynchronous one keeps a connection pool per worker.

## HTTP fixture

A Go `net/http` server (`infra/http`) that answers each item request after
10 ms (`BENCH_HTTP_DELAY_MS`) with deterministic JSON, and counts accepted
connections and requests on `/health`. The HTTP workloads reuse one HTTPX
client per worker, except `http-no-reuse`, which creates one per request.

## PgBouncer

Optional: `BENCH_PGBOUNCER=1` routes PostgreSQL through PgBouncer in
transaction mode. See [PgBouncer](pgbouncer.md).

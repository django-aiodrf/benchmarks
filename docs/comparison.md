# Seven-profile comparison

`scripts/run-comparison.sh` measures eight workloads with one worker per
server. FastAPI, Litestar, Ninja, aiodrf and aiodrf-fastdrf each run on both
Uvicorn (uvloop + httptools) and Granian ASGI (uvloop, default runtime and
queues). DRF and drf-fastdrf each run on Gunicorn gthread with eight threads
and Granian WSGI with four blocking threads. This gives 14 framework/server
pairs. All servers use HTTP/1.1 keep-alive and no access logging, with direct
connections and no reverse proxy. See [exact server settings](servers.md).

## Profiles

| Profile | Validation and output | Database access |
| --- | --- | --- |
| `fastapi` | Pydantic input and response models | SQLAlchemy async ORM, psycopg async |
| `litestar` | msgspec input/output structs; Litestar's msgspec encoder | SQLAlchemy async ORM, psycopg async |
| `ninja` | Pydantic schemas and Ninja pagination | Django async queryset API |
| `drf` | DRF serializers and JSON parser/renderer | Synchronous Django ORM |
| `drf-fastdrf` | fastdrf serializers, msgspec compiler/parser/renderer, dispatch mixin and DataResponse | Synchronous Django ORM |
| `aiodrf` | aiodrf serializers with the default DRF backend and JSON transport | Django async queryset API |
| `aiodrf-fastdrf` | msgspec compiler/parser/renderer, DataResponse, inline representation, 32 reusable request threads | Django async queryset API |

All DRF-family profiles use the same JSON-only REST_FRAMEWORK configuration,
with authentication enabled on the JWT endpoints. The fastdrf profiles use
strict serializer parity, cached compiled field copies and delegated fields.
Tests require all output model serializers to be eligible for msgspec compilation.
The plain input Serializer may use DRF fallback. The aiodrf-fastdrf profile
also opts into aiodrf runtime settings; its difference from aiodrf is not
attributable to the compiler alone. `aiodrf-tuned` remains a legacy name for
the same configuration, outside this run.

## Database contract

Django migrations and `seed_benchmark` own the schema and data. SQLAlchemy
maps those existing tables; it does not create a separate dataset. There are
1,000 articles, ten authors, five tags and one JWT user. Every list is ordered
by primary key and tags are ordered too.

| Scenario | Work performed |
| --- | --- |
| `json` | Encode a small message on every request |
| `json-10k` | Encode a 10 KiB message on every request |
| `db` | Read and serialize ten authors; one SELECT |
| `articles` | Count all articles and return the first twenty with author and tags; three SELECTs |
| `article-detail` | Joined article/author and prefetched tags; two SELECTs |
| `article-create` | Validate input, check author/tags, insert article and two tag links in a transaction, read back related data |
| `jwt-article` | Verify HS256 JWT, load user from PostgreSQL, read article with author and tags; three SELECTs |
| `jwt-articles` | Verify JWT, load user, count articles, return page two of twenty; four SELECTs |

Django uses `select_related` and `prefetch_related`; SQLAlchemy uses
`joinedload` and `selectinload`. SQLAlchemy relationships reject implicit lazy
loads. Tests verify the read query budgets and that representation performs
no extra SQL. The ORMs generate their own SQL and write statements; write
query counts and transaction management are not forced to match.

The same psycopg driver is used synchronously by Django and asynchronously by
SQLAlchemy. Each worker has at most ten database connections. Django uses its
psycopg pool with CONN_MAX_AGE=0. SQLAlchemy uses an AsyncEngine with pool_size=10,
max_overflow=0 and a separate AsyncSession per request, disposed through the
application lifespan. SQLAlchemy reads use its normal implicit transactions;
Django reads use autocommit. Django async querysets still adapt synchronous
ORM work through threads. These results compare application stacks, not just
router overhead. The shared harness initializes Django once even for FastAPI
and Litestar, so their RSS includes that setup; their request paths use only
SQLAlchemy for database work. Startup time is outside the timed sample. See the [SQLAlchemy async API](https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html)
and [Litestar dependency injection](https://docs.litestar.dev/2/usage/dependency-injection.html).

## Measurement

The default run has three independent server starts per framework/server pair
and scenario, 64 concurrent connections, two seconds of warmup and twenty
seconds per read sample. This is a closed-loop load test: each connection
sends its next request after the response. It does not model an independently
arriving production workload.
Writes send exactly 3,000 requests. PostgreSQL is reset before database
samples and again after write warmup. Its `synchronous_commit=off` setting
applies to every profile; this does not measure durable-commit latency.

The server is pinned to CPU 0, oha to CPUs 8–15 and PostgreSQL to CPUs 16–17.
The published run also pins the runner to CPU 18 with `taskset -c 18`.
The server's SMT sibling is not assigned to the benchmark. Adapt these CPU
sets for other hosts. The script starts only PostgreSQL and disables the
legacy MongoDB, Elasticsearch, cache and HTTP clients in application workers.
Other already-running containers are not stopped; their CPU counters may
appear in the raw diagnostics, but none is called by the selected workloads.

Before and after each sample, the runner compares response JSON with the
fixture, checks JWT rejection and verifies persisted writes. Pagination
wrappers may differ by framework; the items, ordering and total must match.
Every load response must have its expected status. Server errors invalidate
the sample. This is JSON-value equality, not byte equality across renderers.
The small JSON response is 27 bytes and the large one 10,254 bytes with the
compact renderers; Ninja's formatting adds one byte to each. Each framework
returns the same byte count on both of its servers.

The report gives throughput medians, spread, p50/p95/p99 latency, server CPU
and memory, service CPU and load-generator CPU. Spread is half the difference
between the highest and lowest throughput divided by the median; three starts
are not a confidence interval. Near ties should be read alongside their spread.
Servers are separate result groups, never pooled or selected after the run.
Source hashes,
package versions, per-profile settings and exact server commands are in the
manifest. No streaming, cache, MongoDB or Elasticsearch workload is included.

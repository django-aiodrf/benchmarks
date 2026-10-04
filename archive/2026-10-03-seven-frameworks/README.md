# Python API stack benchmarks

Archived 3 October 2026 run. [Current report](../../README.md).
The results and run artifacts below are preserved; documentation and source links refer to the current checkout.

Seven profiles serving the same JSON and PostgreSQL workloads: FastAPI with
Pydantic and SQLAlchemy async, Litestar with msgspec and SQLAlchemy async,
Django Ninja, DRF, DRF with fastdrf, aiodrf and aiodrf with fastdrf.

## Latest results

[REPORT.md](REPORT.md) contains the 3 October 2026 run: **7 profiles, 8
scenarios, 1 worker and 2 independent starts per group**. All **112 samples /
56 groups passed**. Median throughput spread between starts: **±2.5%**.
The run took 25.7 minutes. PostgreSQL's highest sample CPU utilization
was 21.7% of its assigned CPUs; oha's was 4.2%.

| Setting | Value |
| --- | --- |
| ASGI | Uvicorn 0.54.0, 1 worker, uvloop + httptools |
| WSGI | Gunicorn 26.2.0, 1 gthread worker, 8 threads |
| Load | oha, 64 concurrent connections; 2 s warmup + 10 s read samples; 3,000 requests per write sample |
| Dataset | PostgreSQL 18.6: 1,000 articles, 10 authors, 5 tags, 1 JWT user |
| Connection limit | 10 per worker; SQLAlchemy max_overflow=0; no PgBouncer |
| CPU layout | Server CPU 0, load CPUs 8–15, PostgreSQL CPUs 16–17 |
| Host | Intel Core i7-14700F, 28 logical CPUs, 123 GiB RAM; Linux 7.0.0; powersave governor |
| Python | CPython 3.14.7, GIL enabled |
| Frameworks | Django 6.1.1, DRF 3.18.1, aiodrf 0.0.4, fastdrf 0.5.0, Ninja 1.7.1, FastAPI 0.142.2, Litestar 2.24.0 |
| Serialization / ORM | Pydantic 2.13.5, msgspec 0.22.0, SQLAlchemy 2.1.3; exact dependencies in [requirements.lock](report/requirements.lock) |

Throughput in requests per second, median over two independent starts:

| Scenario | FastAPI | Litestar | Ninja | DRF | DRF + fastdrf | aiodrf | aiodrf + fastdrf |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `json` | 21,845 | 35,180 | 2,163 | 5,106 | 5,782 | 2,306 | 3,877 |
| `json-10k` | 20,134 | 28,905 | 2,202 | 4,386 | 5,526 | 2,082 | 3,671 |
| `db` | 2,088 | 2,519 | 1,226 | 1,585 | 1,841 | 991 | 1,509 |
| `articles` | 669 | 753 | 391 | 395 | 569 | 308 | 456 |
| `article-detail` | 1,017 | 1,177 | 690 | 724 | 917 | 577 | 842 |
| `article-create` | 522 | 551 | 356 | 362 | 421 | 306 | 389 |
| `jwt-article` | 802 | 861 | 559 | 550 | 708 | 418 | 628 |
| `jwt-articles` | 525 | 576 | 354 | 347 | 466 | 293 | 353 |

![Throughput relative to the fastest profile, one worker](report/graphs/overview-1-worker.png)

The detailed report includes per-profile spread, latency and resource usage.
Near ties should be read alongside the spread; two starts are not a confidence
interval. These are complete application-stack comparisons: SQLAlchemy's async
sessions and Django's ORM/thread adaptation differ, as do framework serializers
and the WSGI/ASGI servers. They do not isolate router speed or the effect of a
single optimization.

The [previous report](../2026-10-01-aiodrf-0.0.1/REPORT.md),
[manifest](../2026-10-01-aiodrf-0.0.1/report/manifest.json) and
[samples](../2026-10-01-aiodrf-0.0.1/report/samples.json) are archived.
That run used aiodrf 0.0.1, a broader workload matrix, and 1/4 workers;
compare profiles within a run rather than treating the runs as a version regression test.

## Profiles

| Profile | Server | Data access and serialization |
| --- | --- | --- |
| `fastapi` | Uvicorn ASGI | SQLAlchemy async + psycopg async; Pydantic input and response models |
| `litestar` | Uvicorn ASGI | SQLAlchemy async + psycopg async; msgspec input/output structs |
| `ninja` | Uvicorn ASGI | Django async querysets; Pydantic schemas |
| `drf` | Gunicorn gthread | Django ORM; DRF serializers and JSON parser/renderer |
| `drf-fastdrf` | Gunicorn gthread | Django ORM; fastdrf serializers, msgspec compiler/parser/renderer, dispatch optimizations and DataResponse |
| `aiodrf` | Uvicorn ASGI | Django async querysets; aiodrf serializers with the default DRF backend |
| `aiodrf-fastdrf` | Uvicorn ASGI | Django async querysets; msgspec compiler/parser/renderer, DataResponse, inline representation and 32 reusable request threads |

DRF-family profiles share a JSON-only REST_FRAMEWORK configuration. The
fastdrf profiles use strict parity and compiled field copies. The aiodrf-fastdrf
profile also enables aiodrf runtime options; all settings are recorded in the
[manifest](report/manifest.json).

## Scenarios

| Scenario | Contract |
| --- | --- |
| `json`, `json-10k` | Encode a small or 10 KiB JSON message on every request |
| `db` | Ten ordered authors from PostgreSQL |
| `articles` | Total count and first page of twenty articles, with author and tags |
| `article-detail` | One article with author and tags |
| `article-create` | Validate input, check references, insert article and two tag links in a transaction, read it back |
| `jwt-article` | Verify HS256 JWT, load the user from PostgreSQL, read article and relations |
| `jwt-articles` | Verify JWT, load the user, count and return the second page of twenty articles |

All profiles use the same seeded tables and return the same JSON values.
Pagination wrappers follow each framework; items, order and total match.
Before and after every sample, the runner checks the response contract, JWT
rejection and persisted writes. Every load response must have the expected
status. SQL query budgets and serialization without lazy queries are tested.

MongoDB, Elasticsearch, cache, streaming and HTTP dependency workloads are not
part of this comparison. Their legacy adapters remain available through the
broader suite. See [comparison methodology](../../docs/comparison.md) for ORM,
transaction, pooling and validation details.

## Run

Requires Linux, Docker Compose, [uv](https://docs.astral.sh/uv/) and
[oha](https://github.com/hatoo/oha). Adapt the CPU sets to your host before running.

```sh
uv venv --python 3.14 .venv
uv pip sync --python .venv/bin/python requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
scripts/run-comparison.sh
```

The script starts only the owned PostgreSQL service, resets the benchmark
rows and writes a new `results/<run>/` directory. It disables unused service
clients in application workers. Existing unrelated services are not stopped.

```sh
REPEATS=3 DURATION=10 scripts/run-comparison.sh
REPEATS=1 DURATION=2 WARMUP=1 POST_REQUESTS=100 scripts/run-comparison.sh --scenarios json db
```

| Variable | Default |
| --- | --- |
| `REPEATS`, `DURATION`, `WARMUP` | 2 starts, 10 seconds, 2 seconds |
| `CONCURRENCY`, `POST_REQUESTS` | 64, 3000 |
| `SERVER_CPUS`, `LOAD_CPUS`, `BENCH_PG_CPUS` | `0`, `8-15`, `16-17` |
| `BENCH_PG_POOL_MAX` | 10, for both ORMs |
| `OUTPUT` | `results/comparison-<time>` |

## Artifacts and tests

Each run includes `REPORT.md`, `summary.csv`, `samples.json`, `manifest.json`,
graphs, and each sample's oha output, command, server log and resource samples.
The manifest records package versions, source hashes, profile settings, server
commands and database settings. Published artifacts are in [report/](report/manifest.json).

```sh
uv pip install --python .venv/bin/python --group dev
BENCH_DATABASE=sqlite BENCH_SERVICE_CLIENTS=0 .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

See [contributing](../../CONTRIBUTING.md) for the seven-profile PostgreSQL HTTP tests.
Do not run tests that reset the database during a measurement.

[Frameworks](../../docs/frameworks.md) · [Methodology](../../docs/methodology.md) ·
[Servers](../../docs/servers.md) · [Infrastructure](../../docs/infrastructure.md) ·
[Configuration](../../docs/configuration.md)

BSD 3-Clause: [LICENSE](../../LICENSE). [NOTICE.md](../../NOTICE.md) credits the original project.

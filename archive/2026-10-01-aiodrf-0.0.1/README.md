> Archived results. See the [current README](../../README.md) and [archive notes](ARCHIVE.md).

# Django API framework benchmarks

HTTP benchmarks of the Python frameworks that serve APIs on Django's ORM and
ecosystem: [aiodrf](https://github.com/django-aiodrf/django-aiodrf), Django
REST framework, adrf, Django Ninja, Django Bolt and plain Django views, with
FastAPI and Litestar on the same Django ORM for reference.

The benchmarks measure **aiodrf 0.0.4** with django-fastdrf 0.5.0, the
releases published on PyPI as `django-aiodrf` and `django-fastdrf`. The other
frameworks are their latest releases at the time of the run;
`requirements.lock` pins every version. The latest report below was measured
with aiodrf 0.0.1.

## Latest results

[REPORT.md](REPORT.md) is the report of the run of 1 October 2026: 22 server
configurations, 35 workloads, two independent server starts per
configuration.

| | |
| --- | --- |
| Frameworks | aiodrf (default and tuned settings), adrf, Django Ninja and plain async Django on Uvicorn; DRF and plain synchronous Django on Gunicorn's sync and gthread workers, and synchronous Django on Uvicorn too; Django Bolt on its own server |
| Workers | 1 and 4, one dedicated CPU each |
| Load | oha 1.16.0, 64 requests in flight; 5 s per read sample after a 2 s warmup; 3,000 requests per write sample |
| Host | Intel Core i7-14700F (8 performance and 12 efficiency cores, 28 logical CPUs), 123 GiB of memory, CPU frequency governor `powersave` |
| Software | Linux 7.0.0, CPython 3.14.7 with the GIL, Django 6.1.1, DRF 3.18.1, Uvicorn 0.54.0, Gunicorn 26.2.0 |
| Services | PostgreSQL 18.6, MongoDB 8.0.4, Elasticsearch 9.5.4, Valkey 9.0.6, each on its own CPUs |

All 770 groups are valid. Two scenarios, `http-no-reuse` and `jwt-article`,
were measured again after fixes to the benchmark; the report's
*Corrections* section says why. One group was limited by a service rather
than by the framework: `mongo-native-aggregate` for Django Bolt at four
workers, where MongoDB used 99% of its CPUs ([methodology](docs/methodology.md#services-must-not-limit-the-result)).

The numbers hold for this host and these settings only. Compare
configurations within the report, not with numbers from other machines.

![Throughput relative to the fastest configuration, four workers](report/graphs/overview-4-workers.png)

## Frameworks and servers

The suite can measure 50 configurations: each framework on every server it
runs on, with one and four workers. The latest run used the subset above
(`scripts/run-core-full.sh`).

| Profile | Framework | Interface | Servers |
| --- | --- | --- | --- |
| `aiodrf` | aiodrf 0.0.4, default settings | ASGI | Uvicorn, Granian |
| `aiodrf-tuned` | aiodrf 0.0.4 with its opt-in fast paths ([settings](docs/frameworks.md#aiodrf)) | ASGI | Uvicorn, Granian |
| `adrf` | adrf 0.1.14 on DRF 3.18.1 | ASGI | Uvicorn, Granian |
| `ninja` | Django Ninja 1.7.1 | ASGI | Uvicorn, Granian |
| `django` | Plain Django 6.1.1, async views | ASGI | Uvicorn, Granian |
| `fastapi` | FastAPI 0.142.2 on the Django ORM | ASGI | Uvicorn, Granian |
| `litestar` | Litestar 2.24.0 on the Django ORM | ASGI | Uvicorn, Granian |
| `drf` | Django REST framework 3.18.1 | WSGI | Granian; Gunicorn sync, gthread and gevent |
| `django-sync` | Plain Django 6.1.1, synchronous views | WSGI and ASGI | Granian; Gunicorn sync, gthread and gevent; Uvicorn |
| `bolt` | Django Bolt 0.11.1 | Its own server | Bolt |

[Frameworks](docs/frameworks.md) describes how each one implements the
workloads, and [servers](docs/servers.md) the exact server settings.

## Workloads

| Group | Scenarios |
| --- | --- |
| JSON | `json` (small object), `json-10k` (10 KiB object) |
| PostgreSQL | `db` (ten rows), `articles` (twenty articles with author and tags, and the count), `article-detail`, `article-create` (validation and a transactional insert) |
| Streaming | `stream-1k`, `stream-10k`: NDJSON streams of eight lines, 1 KiB and 10 KiB |
| Authentication | `jwt-article`, `jwt-articles`: a Bearer JWT, the user loaded from PostgreSQL, then an article or the second page of twenty |
| MongoDB | `mongo-read`, `mongo-aggregate`, `mongo-write` through the Django MongoDB backend; `mongo-native-*` through PyMongo |
| Elasticsearch | `elasticsearch-search`, `-aggregate`, `-write` through django-elasticsearch-dsl; `elasticsearch-native-*` through the Elasticsearch client |
| Cache (Valkey) | `cache-read`, `cache-read-many`, `cache-write`, `cache-pipeline`; cached SQL payloads: `db-cache-hit`, `db-cache-miss`, `articles-cache-hit`, `articles-cache-miss` |
| HTTP dependency | `http-single`, `http-fanout` (three concurrent calls), `http-no-reuse` (a new connection per request) |
| Mixed | `mixed-inline`, `mixed-thread`: the fanout, then pure-Python CPU work inline or in a thread pool |

Every response is checked against a fixed contract before and after each
measurement: the same JSON for every framework (for paginated lists, the same
items and total in the framework's own page shape), the same NDJSON bytes, a
401 without a valid token, and the rows each write stores.

## Method

All frameworks run the same queries through the same data access code
(`bench/domain.py`, `bench/workloads.py`) and the same Django connection
handling. Validation, serialization, authentication, pagination and
streaming use each framework's own tools: DRF serializers, Pydantic models,
msgspec structs, Bolt's JWT validation.

A server gets one CPU per worker; the load generator and each service have
CPUs of their own, and the CPU use of every service and of the load generator
is recorded for every sample. Each sample starts a fresh server, resets the
data it writes and warms up before it is timed; the order of the
configurations is shuffled for each scenario and repetition.
[Methodology](docs/methodology.md) gives the details and the limits.

## Running the benchmarks

Requirements: Linux, [uv](https://docs.astral.sh/uv/), Docker with Compose,
[oha](https://github.com/hatoo/oha) 1.16 or later, and 28 logical CPUs for
the default CPU layout ([adapting it](docs/methodology.md#cpu-layout)).

```sh
uv venv --python 3.14 .venv
uv pip sync --python .venv/bin/python requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
uv pip install --python .venv/bin/python --group dev
```

Each script starts the services (`compose.yaml`), prepares the datasets, runs
its selection twice and writes the report:

| Script | Selection | Duration on the reference host |
| --- | --- | --- |
| `scripts/run-core.sh` | The configurations of the latest results, eight core workloads | about 1 hour |
| `scripts/run-core-full.sh` | The configurations of the latest results, all 35 workloads | about 3.5 hours |
| `scripts/run-suite.sh` | All 50 configurations, all 35 workloads | about 10 hours |

Arguments after a script's name narrow its selection, and environment
variables change its settings:

```sh
scripts/run-suite.sh --frameworks aiodrf-tuned ninja fastapi --scenarios json db articles
REPEATS=3 DURATION=10 scripts/run-core.sh
```

`aiodrf-bench run --help` lists every option; [configuration](docs/configuration.md)
describes the options and the environment variables.

## Run directories

A run writes to `results/<name>/`: `REPORT.md`, `summary.csv`, the charts in
`graphs/`, every sample in `samples.json`, each sample's load generator
output and server log, and `manifest.json` with the complete environment
(package versions, source hashes, server commands, service versions and
settings). `aiodrf-bench report results/<name>` rebuilds the report, and
`aiodrf-bench merge` replaces the scenarios of one run with those a later run
measured again, recording why in the report.

## Documentation

- [Methodology](docs/methodology.md): measurement procedure, CPU layout, service limits
- [Frameworks](docs/frameworks.md): how each framework implements the workloads
- [Servers](docs/servers.md): server settings and worker models
- [Infrastructure](docs/infrastructure.md): service versions, CPUs and settings
- [Configuration](docs/configuration.md): runner options and environment variables
- [NoSQL paths](docs/nosql-paths.md): Django integrations and native clients
- [PgBouncer](docs/pgbouncer.md): the optional transaction-pooling route
- [Profiling](docs/profiling.md): per-scenario CPU profiles
- [Contributing](CONTRIBUTING.md)

## License

BSD 3-Clause; see [LICENSE](LICENSE). [NOTICE.md](NOTICE.md) credits the
project this work started from.

# Configuration

## Commands

```sh
aiodrf-bench prepare --reset-database          # migrate and seed every service
aiodrf-bench run --reset-database --output results/<name> [options]
aiodrf-bench report results/<name>              # rebuild REPORT.md and graphs
aiodrf-bench merge results/<run> results/<rerun> --output results/<name> --reason "..."
aiodrf-bench serve --framework litestar --server granian-asgi --workers 4
aiodrf-bench verify --url http://127.0.0.1:8100 --scenarios json db articles
```

`serve` starts one configuration for manual inspection; `verify` checks the
contracts of the selected scenarios against a running server (writes included
unless `--read-only`).

`merge` copies a run with the scenarios that a later run measured again
replaced by the later samples, and writes its report. The later run must
have the same frameworks, servers, workers, repetitions, duration and
concurrency; the reason given is recorded in the manifest and printed in the
report's *Corrections* section.

## `aiodrf-bench run`

| Option | Default | Meaning |
| --- | --- | --- |
| `--frameworks` | all ten | Profiles to measure |
| `--servers` | `all` | `all`: every server each framework supports; `default`: its first one; or server names |
| `--workers` | `1 4` | Worker counts, each a separate group |
| `--scenarios` | all 35 | Workloads |
| `--repeats` | `3` | Fresh server starts per group |
| `--duration` | `10` | Seconds of load per read sample |
| `--warmup` | `2` | Seconds of warmup before each sample; `0` disables |
| `--concurrency` | `64` | Requests in flight |
| `--post-requests` | `3000` | Requests per write sample |
| `--server-cpus` | none | CPUs for the server, one per worker: `0,2,4,6` or `0-3` |
| `--load-cpus` | none | CPUs for oha, with one thread per CPU |
| `--load-threads` | `2` | oha threads without `--load-cpus` |
| `--seed` | `42` | Seed of the configuration order |
| `--case-timeout` | `600` | Seconds allowed for a write sample |
| `--output` | required | A new directory; an existing one is refused |
| `--reset-database` | required | Confirms that the benchmark's data may be reset |
| `--mode` | `baremetal` | `docker` runs the server in the application image (below) |
| `--oha` | `oha` | The load generator executable |

`scripts/run-suite.sh` runs the whole matrix with `--repeats 2 --duration 5`
and the default CPU layout; its environment variables (`REPEATS`,
`DURATION`, `WARMUP`, `CONCURRENCY`, `POST_REQUESTS`, `WORKERS`,
`SERVER_CPUS`, `LOAD_CPUS`, `OUTPUT`) override them. `scripts/run-core.sh`
runs a short selection with the same settings (see its header).

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `BENCH_DATASET_SIZE` | `1000` | Articles seeded in PostgreSQL (20–100,000) |
| `BENCH_DOCUMENTS` | `200` | Documents seeded in MongoDB and Elasticsearch (20–100,000) |
| `BENCH_PG_HOST`, `BENCH_PG_PORT` | `127.0.0.1`, `55441` | PostgreSQL |
| `BENCH_PG_USER`, `BENCH_PG_PASSWORD` | `bench`, `benchmark-local-only` | PostgreSQL credentials |
| `BENCH_PG_POOL_MIN`, `BENCH_PG_POOL_MAX` | `1`, `10` (`8` with PgBouncer) | psycopg pool per worker |
| `BENCH_PGBOUNCER`, `BENCH_PGBOUNCER_PORT` | unset, `56432` | `1` routes PostgreSQL through PgBouncer |
| `BENCH_MONGO_URL` / `BENCH_MONGO_PORT` | `mongodb://127.0.0.1:57027` / `57027` | MongoDB |
| `BENCH_ES_URL` / `BENCH_ES_PORT` | `http://127.0.0.1:59200` / `59200` | Elasticsearch |
| `BENCH_VALKEY_URL` / `BENCH_VALKEY_PORT` | `valkey://127.0.0.1:56379/0` / `56379` | Valkey |
| `BENCH_HTTP_URL` / `BENCH_HTTP_PORT` | `http://127.0.0.1:58090` / `58090` | HTTP fixture |
| `BENCH_HTTP_DELAY_MS` | `10` | Fixture response delay (restart its container after a change) |
| `BENCH_CLIENT_POOL` | `64` | MongoDB, Elasticsearch, Valkey and HTTP pool size per worker (1–256) |
| `BENCH_CPU_ROUNDS` | `50000` | Iterations of the CPU work in the mixed workloads |
| `BENCH_CPU_THREADS` | `4` | Thread pool size of `mixed-thread`, per worker |
| `BENCH_PG_IO_METHOD` | `worker` | PostgreSQL 18 `io_method` (restart the service after a change) |
| `BENCH_PG_CPUS`, `BENCH_ES_CPUS`, `BENCH_MONGO_CPUS`, `BENCH_VALKEY_CPUS`, `BENCH_HTTP_CPUS`, `BENCH_PGBOUNCER_CPUS` | see [CPU layout](methodology.md#cpu-layout) | Service cpusets, read by Compose |
| `BENCH_HTTP_TESTS` | unset | `1` enables the real-server contract tests |
| `BENCH_TEST_SCENARIOS` | the core scenarios | Scenarios of those tests: a comma-separated list or `all` |

The runner sets `BENCH_PROFILE` for each server. The benchmark's data lives
in the database `aiodrf_benchmarks` (PostgreSQL and MongoDB), the
Elasticsearch indexes `aiodrf-benchmarks-documents` and
`aiodrf-benchmarks-writes`, and the cache key prefix `aiodrf-benchmarks`.
Do not run two runs, or a run and the HTTP tests, at the same time.

## Service clients

Each worker opens its clients once. ASGI workers do it in the lifespan
(`bench/asgi.py`, `bench.services.resources`): synchronous and asynchronous
HTTPX clients, the Elasticsearch clients, PyMongo's `MongoClient` and
`AsyncMongoClient`, django-valkey's async cache with its own pool, and thread
pools for the fanout and the CPU work. WSGI workers open the synchronous
clients in `bench.wsgi.create_application`. The clients are closed when the
worker stops.

## Result files

| File | Content |
| --- | --- |
| `REPORT.md`, `graphs/` | The report and its charts (PNG and SVG) |
| `summary.csv` | One row per group: medians, spread, latency, CPU, memory, busiest service, load generator utilization |
| `samples.json` | Every sample, valid or not, with its error |
| `manifest.json` | Parameters, package versions, source hashes, server commands, service versions and settings, host |
| `<repeat>-<scenario>-<framework>-<server>-w<workers>/` | oha's JSON output and command, the server log, resource samples, verification records |
| `compose.yaml`, `infra/` | Copies of the service configuration used |

## Docker mode

`--mode docker` runs each server in the application image instead of on the
host, with `--cpus` and `--memory` limits; services and oha stay on the host.

```sh
docker build -t aiodrf-benchmarks:local .
aiodrf-bench run --mode docker --image aiodrf-benchmarks:local --reset-database \
  --scenarios json db articles --output results/docker
```

The runner resolves the image to its ID and refuses an image whose benchmark
source differs from the checkout. Compare Docker and host runs separately.

# Contributing

Issues and pull requests are welcome: a framework or server missing from the
comparison, a workload, an adapter that does not use its framework the way
the framework's documentation recommends, or a flaw in the method.

Continuous integration runs the lint, the tests that need no services and the
HTTP fixture's tests. On a pull request from a contributor it starts after a
maintainer approves the run.

## Development environment

```sh
uv venv --python 3.14 .venv
uv pip sync --python .venv/bin/python requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
uv pip install --python .venv/bin/python --group dev
```

After changing `pyproject.toml`, regenerate the lock file with the command at
its top.

## Tests

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
BENCH_DATABASE=sqlite .venv/bin/pytest -q
```

These run without services. The real-server contract tests start every
configuration (62, or those selected with `-k`) and check every scenario
against the services:

```sh
docker compose -p aiodrf-benchmarks up -d --wait
BENCH_HTTP_TESTS=1 BENCH_TEST_SCENARIOS=all .venv/bin/pytest -q tests/test_http.py
BENCH_HTTP_TESTS=1 BENCH_TEST_SCENARIOS=all .venv/bin/pytest -q tests/test_http.py -k "fastapi and w4"
go -C infra/http test ./...
```

They reset the benchmark data; do not run them during a measurement.

## Adding a framework

1. Add the profile to `INTERFACES` in `bench/profiles.py` (`asgi`, `wsgi` or
   `native`); `bench/servers.py` derives its servers from the interface.
2. Write `bench/adapters/<name>.py` with every route of `bench/urls.py`
   (`ROUTES`, the service workloads and the streams), using the framework's
   own validation, serialization, authentication and pagination. Call the
   shared data access in `bench/domain.py` and the service operations through
   `bench.services.request_resources`; do not optimize a query for one
   framework.
3. A Django application is routed in `bench/urls.py`. Any other ASGI
   application is selected in `bench/asgi.py` and wrapped with
   `with_django_request_signals` if it uses the Django ORM. A native server
   needs its command in `bench/servers.py`.
4. Run the contract tests for the new profile on every server and worker
   count, then add it to the tables of the README and `docs/frameworks.md`.

## Adding a workload

1. Add it to `bench/profiles.py`: a scenario tuple, `PATHS`, `DESCRIPTIONS`,
   and `required_services` if it needs a service.
2. Implement the operation once, in `bench/domain.py` or `bench/workloads.py`,
   and route it in every adapter.
3. Add its contract to `bench/verify.py`: the exact response, and for a write
   the persisted state (`bench/infrastructure.py`).
4. Add a unit test of the contract, and run the contract tests on every
   configuration.

## Results

When a pull request claims a performance difference, attach the run
directory's `REPORT.md`, `summary.csv`, `samples.json` and `manifest.json`,
with the exact command. Compare configurations measured in the same run;
numbers from different hosts or runs are not comparable. Keep invalid samples
in the results.

## Current comparison

Run the seven-profile PostgreSQL HTTP contracts on all 14 framework/server
pairs (including concurrent JWT and pagination reads) before a measurement:

```sh
docker compose -p aiodrf-benchmarks up -d --wait postgres
BENCH_SERVICE_CLIENTS=0 BENCH_HTTP_TESTS=1 BENCH_HTTP_MATRIX=comparison \
  BENCH_TEST_SCENARIOS=json,json-10k,db,articles,article-detail,article-create,jwt-article,jwt-articles \
  .venv/bin/pytest -q tests/test_http.py
scripts/run-comparison.sh
```

The HTTP tests reset the owned benchmark database; do not run them during
a measurement. `tests/test_sqlalchemy_store.py` checks async ORM query budgets,
eager loading and rollback; `tests/test_comparison.py` checks the server matrix
and compiled output serializers. See [comparison methodology](docs/comparison.md).

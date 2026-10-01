# Changelog

## 0.1.0 - 2026-10-01

First public release.

- Benchmarks aiodrf 0.0.1, Django REST framework, adrf, Django Ninja, Django
  Bolt, plain Django, FastAPI and Litestar on Django 6.1, over 35 workloads:
  JSON, PostgreSQL, streaming, JWT authentication, MongoDB, Elasticsearch,
  Valkey, an HTTP dependency and mixed CPU work.
- Up to 50 configurations: Uvicorn, Granian, Gunicorn (sync, gthread,
  gevent) and Bolt's own server, with one and four workers on dedicated CPUs.
- Every response is verified against a fixed contract; the CPU use of each
  service and of the load generator is recorded for every sample.
- `scripts/run-core.sh`, `scripts/run-core-full.sh` and
  `scripts/run-suite.sh` run the published selections; `aiodrf-bench merge`
  records a correction run in the report.
- [REPORT.md](REPORT.md): the results of 1 October 2026 on an Intel Core
  i7-14700F.

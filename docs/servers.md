# Servers

The current [comparison](comparison.md) selects Uvicorn and Granian for ASGI,
and Gunicorn gthread and Granian for WSGI, with one worker in every case.

A result group is `scenario / framework / server / workers`; servers and
worker counts are never averaged together. `--servers all` (the default)
measures each framework on every server it supports; naming servers selects
among them. Each run's manifest records the exact commands.

| Server | Frameworks | Worker model |
| --- | --- | --- |
| `uvicorn` | ASGI frameworks, `django-sync` | One event loop per worker process |
| `granian-asgi` | ASGI frameworks, `django-sync` | One event loop per worker process, Rust HTTP handling |
| `granian-wsgi` | `drf`, `drf-fastdrf`, `django-sync` | Four blocking application threads per worker process |
| `gunicorn-sync` | `drf`, `drf-fastdrf`, `django-sync` | One request at a time per worker process |
| `gunicorn-gthread` | `drf`, `drf-fastdrf`, `django-sync` | Eight threads per worker process |
| `gunicorn-gevent` | `drf`, `drf-fastdrf`, `django-sync` | Greenlets, up to 256 connections per worker process |
| `bolt-native` | `bolt` | Rust HTTP server, one Python process per worker |

The ASGI frameworks are `aiodrf`, `aiodrf-fastdrf`, `aiodrf-tuned`, `adrf`, `ninja`, `django`,
`fastapi` and `litestar`. Every ASGI server loads `bench.asgi:application`,
whose lifespan opens the worker's service clients; every WSGI server builds
the application in each worker with `bench.wsgi.create_application`, which
opens synchronous clients. `BENCH_SERVICE_CLIENTS=0` disables these unused
clients in the current comparison; SQLAlchemy still owns its pool through
the FastAPI/Litestar lifespan. No server preloads the application in a parent
process.

## Commands

Published one-worker profiles:

```sh
python -m uvicorn bench.asgi:application --host 127.0.0.1 --port 8100 \
  --workers 1 --loop uvloop --http httptools --ws none --lifespan on \
  --backlog 2048 --timeout-keep-alive 5 --no-access-log --log-level warning

python -m granian --interface asgi --host 127.0.0.1 --port 8100 --workers 1 \
  --loop uvloop --http 1 --no-ws \
  --http1-keep-alive --no-http1-pipeline-flush --no-access-log \
  --log-level warning bench.asgi:application

python -m granian --interface wsgi --host 127.0.0.1 --port 8100 --workers 1 \
  --runtime-mode mt --runtime-threads 1 \
  --loop uvloop --http 1 --no-ws --backlog 128 --backpressure 128 \
  --http1-keep-alive --no-http1-pipeline-flush --no-access-log \
  --log-level warning --blocking-threads 4 --factory bench.wsgi:create_application

python -m gunicorn 'bench.wsgi:create_application()' --bind 127.0.0.1:8100 \
  --workers 1 --worker-class gthread --threads 8 --worker-connections 256 \
  --backlog 2048 --keep-alive 5 --timeout 60 --graceful-timeout 10 \
  --log-level warning --error-logfile - --config python:bench.gunicorn_config

```

Gunicorn's `sync` and `gevent` classes use the same command with
`--worker-class sync` or `gevent` and `--threads 1`. The runner prefixes each
command with `taskset` for the server CPUs ([CPU layout](methodology.md#cpu-layout)).

## Notes

Uvicorn runs with uvloop and httptools, which `uvicorn[standard]` installs,
and has no connection limit.

Granian ASGI follows the [maintainer's recommendation](https://github.com/emmett-framework/granian/discussions/663):
use uvloop and leave runtime and queue controls at their defaults. In the
measured Granian 2.8.4 this means `runtime-mode=auto` resolving to `st`, one
runtime thread, `task-impl=asyncio`, backlog 1024 and backpressure 1024 for
one worker. No WSGI blocking-thread setting is applied to ASGI. This is a
documented starting profile, not a claim that one setting is fastest for
every async application.

Granian WSGI uses `mt`, one runtime thread, four blocking threads,
backpressure 128 and backlog 128. Prior direct-connection tests at concurrency
64 found similar throughput to BP256/backlog2048. BP8 with keep-alive caused
connection starvation and timeouts. Granian backpressure limits **connections**,
not concurrent database queries: keep-alive connections retain capacity.
The four blocking threads bound application execution; the database pool
allows at most ten connections per worker. See the
[maintainer's connection-limit explanation](https://github.com/emmett-framework/granian/discussions/765)
and [versioned settings documentation](https://github.com/emmett-framework/granian/blob/v2.8.4/README.md#backpressure).
These WSGI settings were selected for one worker and direct clients, without
a proxy. The current report does not establish settings for multiple workers.

Gunicorn's sync worker closes the connection after every response, so the
load generator opens a new connection for every request; the reconnections
are part of its results. The gevent worker patches the standard library in
its workers; the benchmark adds no patch of its own.

WSGI workers close Django's caches synchronously. When django-valkey is
imported, it replaces Django's `request_finished` cache receiver with an
async one; in a WSGI worker that receiver would run `async_to_sync` after
every request, and it fails under gevent. `bench.wsgi` restores Django's
synchronous receiver, since WSGI workers have no async cache to close.

Plain synchronous Django on an ASGI server runs its views in a worker thread
through Django's ASGI handler. The `django-sync` profile on Uvicorn and on the
WSGI servers runs the same views, so the two can be compared directly.

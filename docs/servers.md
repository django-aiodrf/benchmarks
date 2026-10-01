# Servers

A result group is `scenario / framework / server / workers`; servers and
worker counts are never averaged together. `--servers all` (the default)
measures each framework on every server it supports; naming servers selects
among them. Each run's manifest records the exact commands.

| Server | Frameworks | Worker model |
| --- | --- | --- |
| `uvicorn` | ASGI frameworks, `django-sync` | One event loop per worker process |
| `granian-asgi` | ASGI frameworks, `django-sync` | One event loop per worker process, Rust HTTP handling |
| `granian-wsgi` | `drf`, `django-sync` | Eight application threads per worker process |
| `gunicorn-sync` | `drf`, `django-sync` | One request at a time per worker process |
| `gunicorn-gthread` | `drf`, `django-sync` | Eight threads per worker process |
| `gunicorn-gevent` | `drf`, `django-sync` | Greenlets, up to 256 connections per worker process |
| `bolt-native` | `bolt` | Rust HTTP server, one Python process per worker |

The ASGI frameworks are `aiodrf`, `aiodrf-tuned`, `adrf`, `ninja`, `django`,
`fastapi` and `litestar`. Every ASGI server loads `bench.asgi:application`,
whose lifespan opens the worker's service clients; every WSGI server builds
the application in each worker with `bench.wsgi.create_application`, which
opens synchronous clients. No server preloads the application in a parent
process.

## Commands

For four workers (one worker: `--workers 1`, `--processes 1`, and Granian `--runtime-mode st`):

```sh
python -m uvicorn bench.asgi:application --host 127.0.0.1 --port 8100 \
  --workers 4 --loop uvloop --http httptools --ws none --lifespan on \
  --backlog 2048 --timeout-keep-alive 5 --no-access-log --log-level warning

python -m granian --interface asgi --host 127.0.0.1 --port 8100 --workers 4 \
  --runtime-mode mt --runtime-threads 1 \
  --loop uvloop --http 1 --no-ws --backlog 2048 --backpressure 256 \
  --http1-keep-alive --no-http1-pipeline-flush --no-access-log \
  --log-level warning --task-impl asyncio bench.asgi:application

python -m granian --interface wsgi --host 127.0.0.1 --port 8100 --workers 4 \
  --runtime-mode mt --runtime-threads 1 \
  --loop uvloop --http 1 --no-ws --backlog 2048 --backpressure 256 \
  --http1-keep-alive --no-http1-pipeline-flush --no-access-log \
  --log-level warning --blocking-threads 8 --factory bench.wsgi:create_application

python -m gunicorn 'bench.wsgi:create_application()' --bind 127.0.0.1:8100 \
  --workers 4 --worker-class gthread --threads 8 --worker-connections 256 \
  --backlog 2048 --keep-alive 5 --timeout 60 --graceful-timeout 10 \
  --log-level warning --error-logfile - --config python:bench.gunicorn_config

python manage.py runbolt --host 127.0.0.1 --port 8100 --processes 4 --no-admin
```

Gunicorn's `sync` and `gevent` classes use the same command with
`--worker-class sync` or `gevent` and `--threads 1`. The runner prefixes each
command with `taskset` for the server CPUs ([CPU layout](methodology.md#cpu-layout)).

## Notes

Uvicorn runs with uvloop and httptools, which `uvicorn[standard]` installs,
and has no connection limit.

Granian's Rust runtime is single-threaded (`st`) with one worker and
multi-threaded (`mt`) with four, with one runtime thread per worker in both
cases. Its backpressure limit of 256 requests per worker is above the
benchmark's concurrency.

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

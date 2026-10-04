# Frameworks

Every framework answers the same URLs with the same response contract
(`bench/verify.py`). Django profiles share their queries and transactional
insert in `bench/domain.py`; FastAPI and Litestar use
`bench/sqlalchemy_store.py`. The legacy MongoDB, Elasticsearch,
cache and HTTP operations are in `bench/workloads.py`, `bench/native.py` and
`bench/services.py`. What differs is what a framework does itself: routing,
request parsing, validation, serialization, authentication, pagination,
streaming and the response path. Each adapter uses the tools the framework
offers for these, in the way its documentation shows.

| Profile | Adapter | Handlers |
| --- | --- | --- |
| `aiodrf`, `aiodrf-fastdrf`, `aiodrf-tuned` | `bench/adapters/aiodrf.py` | async `APIView`s and a generic `ListAPIView` |
| `adrf` | `bench/adapters/adrf.py` | async `APIView`s and a generic `ListAPIView` |
| `drf`, `drf-fastdrf` | `bench/adapters/drf.py` | `APIView`s and a generic `ListAPIView` |
| `ninja` | `bench/adapters/ninja.py` | async operations |
| `django`, `django-sync` | `bench/adapters/django.py` | async and synchronous function views |
| `fastapi` | `bench/adapters/fastapi.py` | async path operations |
| `litestar` | `bench/adapters/litestar.py` | async route handlers |
| `bolt` | `bench/adapters/bolt.py` | async route handlers |

## Per workload

| Workload | DRF, adrf, aiodrf | Django Ninja | FastAPI | Litestar | Django Bolt | Plain Django |
| --- | --- | --- | --- | --- | --- | --- |
| JSON output | DRF `Response` and `JSONRenderer` | Pydantic response schema | Pydantic `response_model` | msgspec encoding of the returned value | msgspec encoding | `JsonResponse` |
| SQL output | `ModelSerializer` with nested author and tag serializers | Pydantic schemas over a dict projection | Pydantic models over a dict projection | msgspec output structs | the dict projection | the dict projection |
| Input validation (`article-create`) | a DRF `Serializer` | a Pydantic schema | a Pydantic model | a msgspec `Struct` | a msgspec `Struct` | hand-written checks |
| JWT authentication | an authentication class with `IsAuthenticated` | an async `HttpBearer` | an `HTTPBearer` dependency | a dependency (`Provide`) | `JWTAuthentication` and `IsAuthenticated`, validated in Rust | the header, read in the view |
| Pagination (`jwt-articles`) | `ListAPIView` with DRF's `PageNumberPagination` | `@paginate(PageNumberPagination)` | count and slice | `OffsetPagination` | count and slice | count and slice |
| NDJSON streaming | DRF `JSONRenderer` per line; aiodrf's `StreamingResponse` | `StreamingHttpResponse` | `StreamingResponse` | `Stream` | `StreamingResponse` | `StreamingHttpResponse` |

The dict projection is `bench.domain.article_values`: the same fields the
DRF serializers declare. Ninja's paginated list serializes the model
instances through its schema instead, as `@paginate` does.

All profiles decode the token with the same key and algorithm (HS256, a
fixed expiry) and load the user from PostgreSQL; Bolt validates the token with
its own Rust implementation and loads the user with Django's async ORM, as
the other Django-based asynchronous profiles do (see [Django Bolt](#django-bolt)). A
missing or invalid token is a 401 everywhere.

Service workloads (`mongo-*`, `elasticsearch-*`, `cache-*`, `*-cache-*`,
`http-*`, `mixed-*`) call the shared operation and return its result through
the framework's normal JSON response path: they measure dispatch, the client
calls and scheduling, not serialization. Asynchronous frameworks await native
async clients where they exist (PyMongo's `AsyncMongoClient`, the async
Elasticsearch client, django-valkey's async cache, HTTPX); synchronous
frameworks use the synchronous clients. [NoSQL paths](nosql-paths.md)
describes both families.

## aiodrf

The `aiodrf` profile uses aiodrf with its defaults: DRF's serializers and
`JSONRenderer`, awaited through `aiodrf.aio`. The `aiodrf-fastdrf` profile (also available as `aiodrf-tuned`)
enables aiodrf's opt-in fast paths, all of which keep DRF's output:

```python
FASTDRF = {
    "SERIALIZER_BACKEND": "msgspec",
    "SERIALIZER_BACKEND_PARITY": "strict",
    "SERIALIZER_BACKEND_FALLBACK": "drf",
    "DELEGATE_FIELDS": True,
    "CACHE_SERIALIZER_FIELDS": True,
    "FIELD_COPY_MODE": "compiled",
}
AIODRF = {
    "REPRESENTATION_MODE": "inline",
    "REQUEST_THREADS": 32,
}
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "fastdrf.msgspec.renderers.MsgspecJSONRenderer"
]
```

The serializer settings are django-fastdrf's, which aiodrf builds on since
0.0.3. `REQUEST_THREADS` needs aiodrf's `lifespan` extra.

It also answers with `aiodrf.response.DataResponse` and is served by
`aiodrf.asgi.get_asgi_application()`, which `REQUEST_THREADS` requires.
Tests assert that the output model serializers compile. The plain input
serializer may fall back to DRF. Both fastdrf profiles also select
`fastdrf.msgspec.parsers.MsgspecJSONParser`.

## adrf and DRF

adrf runs DRF's authentication in a worker thread (its `initial()` is
synchronous), and its serializers expose `.adata`. DRF is synchronous and is
measured on WSGI servers, where it runs without thread adaptation.

## FastAPI and Litestar

FastAPI uses Pydantic request/response models; Litestar uses msgspec structs.
Both use SQLAlchemy async ORM with psycopg, a worker-owned engine and a
request-scoped session. Their own lifespans dispose the engine. Neither runs
Django request signals or Django ORM work during a measured endpoint.

The [seven-profile comparison](comparison.md) documents query budgets, pool
limits, transaction differences and the selected workloads. DRF-fastdrf uses
fastdrf's serializer classes and DispatchOptimizationMixin, with DataResponse
for unpaginated output. Its paginated ListAPIView retains a DRF Response.

## Django Bolt

Bolt runs its own Rust HTTP server; `--processes` sets the worker count. Two
adapter details are part of its results:

- Its HTTP dispatch loop differs from its lifespan loop, where the service
  clients live. Service operations are submitted to the lifespan loop with
  `asyncio.run_coroutine_threadsafe` and awaited.
- Its parent process checks migrations and closes its database connections
  before forking the workers. With Django's connection pool, closing returns
  a connection to the pool, whose sockets the forked workers would share and
  corrupt. The adapter closes the pools before each fork
  (`close_database_pools`).
- It does not use Bolt's `Depends(get_current_user)`. In django-bolt 0.11.1
  that dependency loads the user on Bolt's default executor, up to 32
  threads, and each thread keeps its database connection. With the psycopg
  pool of 10 connections that Bolt's deployment guide recommends, the
  eleventh thread waits for a connection and the request fails after 10
  seconds with `PoolTimeout`. Measured on `jwt-article` at 64 concurrent
  requests: every sample failed with Bolt's default executor, and none with
  `DJANGO_BOLT_EXECUTOR_THREADS=8` or with a pool of 40. The adapter loads
  the user with `User.objects.filter(pk=...).afirst()` instead, leaving
  Bolt's executor size at its default.

## Plain Django

The `django` profile uses async function views and `JsonResponse`, with the
same checks the frameworks perform written by hand; `django-sync` has the
same views, synchronous. It is measured on the WSGI servers and on the ASGI
servers, where Django runs the synchronous views in a thread.

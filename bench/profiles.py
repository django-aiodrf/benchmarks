"""Frameworks, their server interface, and the workloads every one implements."""

# name -> interface. ASGI frameworks run on Uvicorn and Granian; the
# synchronous ones (DRF, plain Django views) on WSGI servers; Django Bolt on
# its own server. Plain synchronous Django is also measured on the ASGI
# servers (``bench.servers.servers_for``). ``aiodrf-tuned`` is aiodrf with its
# opt-in fast paths; the ``aiodrf`` profile keeps the defaults.
INTERFACES = {
    "aiodrf": "asgi",
    "aiodrf-tuned": "asgi",
    "aiodrf-fastdrf": "asgi",
    "adrf": "asgi",
    "ninja": "asgi",
    "django": "asgi",
    "fastapi": "asgi",
    "litestar": "asgi",
    "drf": "wsgi",
    "drf-fastdrf": "wsgi",
    "django-sync": "wsgi",
    "bolt": "native",
}
PROFILES = tuple(INTERFACES)
# Frameworks that are not Django applications: they use the Django ORM and
# Django's connection handling through ``bench.asgi``, like Django Ninja does.
NON_DJANGO = frozenset(("fastapi", "litestar"))
CORE_SCENARIOS = (
    "json",
    "json-10k",
    "db",
    "articles",
    "article-detail",
    "article-create",
)
SERVICE_SCENARIOS = (
    "mongo-read",
    "mongo-aggregate",
    "mongo-write",
    "mongo-native-read",
    "mongo-native-aggregate",
    "mongo-native-write",
    "elasticsearch-search",
    "elasticsearch-aggregate",
    "elasticsearch-write",
    "elasticsearch-native-search",
    "elasticsearch-native-aggregate",
    "elasticsearch-native-write",
    "cache-read",
    "cache-read-many",
    "cache-write",
    "cache-pipeline",
    "db-cache-hit",
    "db-cache-miss",
    "articles-cache-hit",
    "articles-cache-miss",
    "http-single",
    "http-no-reuse",
    "http-fanout",
    "mixed-inline",
    "mixed-thread",
)
STREAM_SCENARIOS = ("stream-1k", "stream-10k")
AUTH_SCENARIOS = ("jwt-article", "jwt-articles")
EXTENDED_SCENARIOS = STREAM_SCENARIOS + AUTH_SCENARIOS
SCENARIOS = CORE_SCENARIOS + EXTENDED_SCENARIOS + SERVICE_SCENARIOS
SERVICE_WRITES = frozenset(
    (
        "mongo-write",
        "mongo-native-write",
        "elasticsearch-write",
        "elasticsearch-native-write",
        "cache-write",
        "cache-pipeline",
    )
)
WRITE_SCENARIOS = SERVICE_WRITES | {"article-create"}
PATHS = {
    "json": "/json",
    "json-10k": "/json-10k",
    "db": "/db",
    "articles": "/articles",
    "article-detail": "/articles/1",
    "article-create": "/articles",
    "stream-1k": "/stream-1k",
    "stream-10k": "/stream-10k",
    "jwt-article": "/auth/articles/1",
    "jwt-articles": "/auth/articles?page=2",
    **{name: "/workloads/" + name for name in SERVICE_SCENARIOS},
}
CREATE_BODY = {
    "title": "Created article",
    "body": "Benchmark content",
    "author_id": 1,
    "tag_ids": [1, 2],
}
JSON_BODY = {"message": "Hello, world!"}
JSON_10K_BODY = {"message": "x" * 10240}
# Eight NDJSON lines of 128 or 1280 bytes: 1 KiB and 10 KiB per response.
STREAM_LINES = 8
STREAM_LINE_BYTES = {"stream-1k": 128, "stream-10k": 1280}
# The page the paginated case reads: items 21-40.
AUTH_PAGE = 2
AUTH_PAGE_SIZE = 20


def stream_items(scenario: str) -> list[dict]:
    # '{"index":0,"data":""}' and the newline take 22 bytes.
    padding = STREAM_LINE_BYTES[scenario] - 22
    return [{"index": index, "data": "x" * padding} for index in range(STREAM_LINES)]


def required_services(scenarios) -> set[str]:
    services = set()
    for name in scenarios:
        if name in (
            "db",
            "articles",
            "article-detail",
            "article-create",
            *AUTH_SCENARIOS,
        ):
            services.add("postgres")
        elif name.startswith(("db-cache-", "articles-cache-")):
            services.update(("postgres", "valkey"))
        elif name.startswith("cache-"):
            services.add("valkey")
        elif name.startswith("mongo-"):
            services.add("mongodb")
        elif name.startswith("elasticsearch-"):
            services.add("elasticsearch")
        elif name.startswith(("http-", "mixed-")):
            services.add("http")
    return services


def method_for(scenario: str) -> str:
    return "POST" if scenario in WRITE_SCENARIOS else "GET"


def status_for(scenario: str) -> int:
    scenario = integration_scenario(scenario)
    return (
        201
        if scenario in ("article-create", "mongo-write", "elasticsearch-write")
        else 200
    )


def integration_scenario(scenario: str) -> str:
    """Map a direct-client case to its shared data/persistence contract."""
    return scenario.replace("-native-", "-")


# One line per workload, for reports and documentation.
DESCRIPTIONS = {
    "json": "A small JSON object, encoded on every request",
    "json-10k": "A 10 KiB JSON object, encoded on every request",
    "db": "Ten authors from PostgreSQL, ordered",
    "articles": "Twenty articles with author and tags (join and prefetch) and the total count",
    "article-detail": "One article with author and tags",
    "article-create": "Validate input, insert an article with two tags in a transaction, read it back",
    "stream-1k": "An NDJSON stream of eight lines, 1 KiB in total",
    "stream-10k": "An NDJSON stream of eight lines, 10 KiB in total",
    "jwt-article": "Bearer JWT, the user loaded from PostgreSQL, then one article",
    "jwt-articles": "Bearer JWT and user, then the second page of twenty articles",
    "mongo-read": "Twenty documents through the Django MongoDB backend (indexed filter)",
    "mongo-aggregate": "A group-by aggregation through the Django MongoDB backend",
    "mongo-write": "One document inserted through the Django MongoDB backend",
    "mongo-native-read": "The same read through PyMongo",
    "mongo-native-aggregate": "The same aggregation through PyMongo",
    "mongo-native-write": "The same insert through PyMongo",
    "elasticsearch-search": "A filtered, sorted search through django-elasticsearch-dsl",
    "elasticsearch-aggregate": "A terms aggregation through django-elasticsearch-dsl",
    "elasticsearch-write": "One document indexed through django-elasticsearch-dsl",
    "elasticsearch-native-search": "The same search through the Elasticsearch DSL client",
    "elasticsearch-native-aggregate": "The same aggregation through the Elasticsearch DSL client",
    "elasticsearch-native-write": "The same write through the Elasticsearch bulk helper",
    "cache-read": "One value from Valkey through Django's cache API",
    "cache-read-many": "Three values from Valkey in one call",
    "cache-write": "One value written to Valkey",
    "cache-pipeline": "Three values written to Valkey in one pipeline",
    "db-cache-hit": "The db payload from the cache (no SQL)",
    "db-cache-miss": "A cache miss: the db query, then a cache write",
    "articles-cache-hit": "The articles payload from the cache (no SQL)",
    "articles-cache-miss": "A cache miss: the articles queries, then a cache write",
    "http-single": "One call to an HTTP service (10 ms), with a reused client",
    "http-no-reuse": "One call to the HTTP service with a new client per request",
    "http-fanout": "Three concurrent calls to the HTTP service",
    "mixed-inline": "The fanout, then pure-Python CPU work on the request's thread",
    "mixed-thread": "The fanout, then the same CPU work in a thread pool",
}

COMPARISON_PROFILES = (
    "fastapi",
    "litestar",
    "ninja",
    "drf",
    "drf-fastdrf",
    "aiodrf",
    "aiodrf-fastdrf",
)
COMPARISON_SCENARIOS = CORE_SCENARIOS + AUTH_SCENARIOS

"""Django settings shared by every profile; ``BENCH_PROFILE`` selects the framework."""

import os
from pathlib import Path

from bench.profiles import PROFILES

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE = os.environ.get("BENCH_PROFILE", "django")
if PROFILE not in PROFILES:
    raise ValueError(f"Unknown benchmark profile: {PROFILE}")
SECRET_KEY = "benchmark-only-not-for-deployment"
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
MIDDLEWARE = []
# Successful-request access logs are disabled in both native and ASGI servers.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    # The user of the authenticated cases (bench.auth).
    "django.contrib.auth",
    "bench.app",
    "bench.mongoapp",
    "django_elasticsearch_dsl",
]
if PROFILE.startswith("aiodrf"):
    INSTALLED_APPS += ["rest_framework", "aiodrf"]
if PROFILE == "bolt":
    INSTALLED_APPS += ["django_bolt"]
    BOLT_API = ["bench.adapters.bolt:api"]
    BOLT_COMPRESSION = False
ROOT_URLCONF = "bench.urls"
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
BENCH_DATASET_SIZE = int(os.environ.get("BENCH_DATASET_SIZE", "1000"))
BENCH_ALLOW_RESET = os.environ.get("BENCH_ALLOW_RESET") == "1"
BENCH_PGBOUNCER = os.environ.get("BENCH_PGBOUNCER") == "1"
BENCH_PG_POOL_MIN = int(os.environ.get("BENCH_PG_POOL_MIN", "1"))
BENCH_PG_POOL_MAX = int(
    os.environ.get("BENCH_PG_POOL_MAX", "8" if BENCH_PGBOUNCER else "10")
)

if os.environ.get("BENCH_DATABASE", "postgres") == "sqlite":
    # A fixed owned file, used only for correctness smoke checks.
    (BASE_DIR / ".state").mkdir(exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / ".state/smoke.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "aiodrf_benchmarks",
            "USER": os.environ.get("BENCH_PG_USER", "bench"),
            "PASSWORD": os.environ.get("BENCH_PG_PASSWORD", "benchmark-local-only"),
            "HOST": os.environ.get("BENCH_PG_HOST", "127.0.0.1"),
            "PORT": os.environ.get("BENCH_PGBOUNCER_PORT", "56432")
            if BENCH_PGBOUNCER
            else os.environ.get("BENCH_PG_PORT", "55441"),
            "CONN_MAX_AGE": 0,
            "ATOMIC_REQUESTS": False,
            "DISABLE_SERVER_SIDE_CURSORS": BENCH_PGBOUNCER,
            "OPTIONS": {
                "pool": {
                    "min_size": BENCH_PG_POOL_MIN,
                    "max_size": BENCH_PG_POOL_MAX,
                    "timeout": 10,
                },
                # PgBouncer tracks protocol-level prepared statements (limit 200).
                "prepare_threshold": 5,
            },
        }
    }

DATABASES["mongodb"] = {
    "ENGINE": "django_mongodb_backend",
    "HOST": os.environ.get(
        "BENCH_MONGO_URL",
        "mongodb://127.0.0.1:" + os.environ.get("BENCH_MONGO_PORT", "57027"),
    ),
    "NAME": "aiodrf_benchmarks",
    "OPTIONS": {
        "maxPoolSize": int(os.environ.get("BENCH_CLIENT_POOL", "64")),
        "minPoolSize": 0,
        "serverSelectionTimeoutMS": 5000,
        "socketTimeoutMS": 10000,
        "waitQueueTimeoutMS": 5000,
        "retryWrites": False,
        # Acknowledged by the primary without waiting for its journal.
        "w": 1,
        "journal": False,
    },
}
DATABASE_ROUTERS = ["bench.mongoapp.router.MongoRouter"]
CACHES = {
    "default": {
        "BACKEND": "django_valkey.cache.ValkeyCache",
        "LOCATION": os.environ.get(
            "BENCH_VALKEY_URL",
            "valkey://127.0.0.1:" + os.environ.get("BENCH_VALKEY_PORT", "56379") + "/0",
        ),
        "KEY_PREFIX": "aiodrf-benchmarks",
        "OPTIONS": {
            "CONNECTION_POOL_CLASS": "valkey.connection.BlockingConnectionPool",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": int(os.environ.get("BENCH_CLIENT_POOL", "64")),
                "timeout": 5,
            },
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
            "IGNORE_EXCEPTIONS": False,
        },
    },
}
CACHES["async"] = {
    **CACHES["default"],
    "BACKEND": "django_valkey.async_cache.cache.AsyncValkeyCache",
    "OPTIONS": {
        **CACHES["default"]["OPTIONS"],
        "CONNECTION_POOL_CLASS": "valkey.asyncio.connection.BlockingConnectionPool",
        "CONNECTION_FACTORY": "bench.cache.WorkerAsyncConnectionFactory",
        "CLOSE_CONNECTION": True,
    },
}
ELASTICSEARCH_DSL = {
    "default": {
        "hosts": [
            os.environ.get(
                "BENCH_ES_URL",
                "http://127.0.0.1:" + os.environ.get("BENCH_ES_PORT", "59200"),
            )
        ],
        "connections_per_node": int(os.environ.get("BENCH_CLIENT_POOL", "64")),
        "request_timeout": 10,
        "max_retries": 0,
    },
}
# Explicit indexing keeps unrelated PostgreSQL writes free of search side effects.
ELASTICSEARCH_DSL_AUTOSYNC = False
ELASTICSEARCH_DSL_AUTO_REFRESH = False

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": None,
    "COERCE_BIGINT_TO_STRING": False,
}
AIODRF = {}
if PROFILE == "aiodrf-tuned":
    AIODRF = {
        "SERIALIZER_BACKEND": "msgspec",
        "SERIALIZER_BACKEND_PARITY": "strict",
        "SERIALIZER_BACKEND_FALLBACK": "error",
        "CACHE_SERIALIZER_FIELDS": True,
        "FIELD_COPY_MODE": "compiled",
        # These serializers read only already-loaded model fields and relations.
        "REPRESENTATION_MODE": "inline",
        # Keep request threads for later requests (aiodrf.asgi).
        "REQUEST_THREADS": 32,
    }
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
        "aiodrf.contrib.msgspec.renderers.MsgspecJSONRenderer"
    ]

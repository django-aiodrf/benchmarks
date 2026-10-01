#!/usr/bin/env bash
# The frameworks and servers of scripts/run-core.sh over every scenario,
# including the MongoDB, Elasticsearch, streaming and authenticated ones,
# without the gevent worker.
#
# The settings of scripts/run-suite.sh apply; extra arguments go to
# `aiodrf-bench run` and override the selection below.
#
#   scripts/run-core-full.sh
#   REPEATS=3 scripts/run-core-full.sh --workers 4
set -euo pipefail
cd "$(dirname "$0")/.."

exec scripts/run-suite.sh \
  --frameworks aiodrf aiodrf-tuned adrf ninja drf django django-sync bolt \
  --servers uvicorn gunicorn-sync gunicorn-gthread bolt-native \
  "$@"

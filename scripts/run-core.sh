#!/usr/bin/env bash
# A short comparison of the Django API frameworks: the asynchronous ones on
# Uvicorn, the synchronous ones on Gunicorn (sync, gthread and gevent
# workers; plain synchronous Django on Uvicorn too), Django-Bolt on its own
# server, with 1 and 4 workers, over the core workloads.
#
# The settings of scripts/run-suite.sh apply; extra arguments go to
# `aiodrf-bench run` and override the selection below.
#
#   scripts/run-core.sh
#   REPEATS=3 scripts/run-core.sh --scenarios json db
set -euo pipefail
cd "$(dirname "$0")/.."

exec scripts/run-suite.sh \
  --frameworks aiodrf aiodrf-tuned adrf ninja drf django django-sync bolt \
  --servers uvicorn gunicorn-sync gunicorn-gthread gunicorn-gevent bolt-native \
  --scenarios json db articles article-create jwt-articles cache-read db-cache-hit http-fanout \
  "$@"

#!/usr/bin/env bash
# Seven profiles, one worker, PostgreSQL only. Each sample starts a fresh server.
set -euo pipefail
cd "$(dirname "$0")/.."
export BENCH_SERVICE_CLIENTS=0
OUTPUT="${OUTPUT:-results/comparison-$(date -u +%Y%m%dT%H%M%SZ)}"
BENCH="${BENCH:-.venv/bin/aiodrf-bench}"
command -v oha >/dev/null || { echo 'oha is required' >&2; exit 1; }
docker compose -p aiodrf-benchmarks up -d --wait postgres
"$BENCH" run --reset-database \
  --frameworks fastapi litestar ninja drf drf-fastdrf aiodrf aiodrf-fastdrf \
  --servers uvicorn granian-asgi gunicorn-gthread granian-wsgi --workers 1 \
  --scenarios json json-10k db articles article-detail article-create jwt-article jwt-articles \
  --repeats "${REPEATS:-3}" --duration "${DURATION:-20}" --warmup "${WARMUP:-2}" \
  --concurrency "${CONCURRENCY:-64}" --post-requests "${POST_REQUESTS:-3000}" \
  --server-cpus "${SERVER_CPUS:-0}" --load-cpus "${LOAD_CPUS:-8-15}" \
  --output "$OUTPUT" "$@"
echo "Report: $OUTPUT/REPORT.md"

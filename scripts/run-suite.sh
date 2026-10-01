#!/usr/bin/env bash
# Run the complete benchmark matrix: every framework on every server it
# supports, with 1 and 4 workers, over every scenario.
#
# Settings are environment variables; extra arguments go to `aiodrf-bench run`
# (for example `--frameworks aiodrf ninja` or `--scenarios json db`).
#
#   scripts/run-suite.sh
#   REPEATS=3 DURATION=10 scripts/run-suite.sh --frameworks aiodrf-tuned fastapi
#
# The default CPU layout is for an 8 performance-core / 12 efficiency-core
# host (see docs/methodology.md#cpu-layout); adapt SERVER_CPUS, LOAD_CPUS and
# the service CPU variables of compose.yaml to other machines.
set -euo pipefail
cd "$(dirname "$0")/.."

REPEATS="${REPEATS:-2}"
DURATION="${DURATION:-5}"
WARMUP="${WARMUP:-2}"
CONCURRENCY="${CONCURRENCY:-64}"
POST_REQUESTS="${POST_REQUESTS:-3000}"
WORKERS="${WORKERS:-1 4}"
# One CPU per worker, SMT siblings left idle; the load generator on the
# four other performance cores, both SMT threads.
SERVER_CPUS="${SERVER_CPUS:-0,2,4,6}"
LOAD_CPUS="${LOAD_CPUS:-8-15}"
OUTPUT="${OUTPUT:-results/suite-$(date -u +%Y%m%dT%H%M%SZ)}"
BENCH="${BENCH:-.venv/bin/aiodrf-bench}"

command -v oha >/dev/null || { echo "oha is not installed (https://github.com/hatoo/oha)" >&2; exit 1; }
docker compose -p aiodrf-benchmarks up -d --wait
"$BENCH" prepare --reset-database

# shellcheck disable=SC2086 # WORKERS is a list
"$BENCH" run --reset-database \
  --servers all --workers $WORKERS \
  --repeats "$REPEATS" --duration "$DURATION" --warmup "$WARMUP" \
  --concurrency "$CONCURRENCY" --post-requests "$POST_REQUESTS" \
  --server-cpus "$SERVER_CPUS" --load-cpus "$LOAD_CPUS" \
  --output "$OUTPUT" "$@"
echo "Report: $OUTPUT/REPORT.md"

# PgBouncer

By default the application connects to PostgreSQL directly, with a psycopg
pool per worker. `BENCH_PGBOUNCER=1` routes it through PgBouncer in
transaction mode instead; the two routes are separate measurements, recorded
in the manifest.

```sh
docker compose -p aiodrf-benchmarks up -d --wait postgres pgbouncer
.venv/bin/aiodrf-bench run --reset-database --scenarios db articles article-create \
  --server-cpus 0,2,4,6 --load-cpus 8-15 --output results/postgres-direct
BENCH_PGBOUNCER=1 .venv/bin/aiodrf-bench run --reset-database --scenarios db articles article-create \
  --server-cpus 0,2,4,6 --load-cpus 8-15 --output results/postgres-pgbouncer
```

For a like-for-like comparison, give both routes the same application pool
with `BENCH_PG_POOL_MAX=8`.

## Configuration

`infra/pgbouncer.ini`:

| Setting | Value |
| --- | --- |
| `pool_mode` | `transaction` |
| `default_pool_size`, `min_pool_size`, `max_db_connections` | `50` |
| `reserve_pool_size` | `0` |
| `max_client_conn` | `256` |
| `max_prepared_statements` | `200` |
| `query_wait_timeout` | `2` seconds |

With PgBouncer, Django uses `DISABLE_SERVER_SIDE_CURSORS = True` (a later
transaction may run on another server connection), psycopg's
`prepare_threshold = 5` (PgBouncer tracks protocol-level prepared statements)
and a pool of at most 8 connections per worker. The benchmark uses no
session-level state: no advisory locks, temporary tables or `SET`. The
credentials in `infra/pgbouncer-users.txt` are for this local service only.

See [Django's notes on transaction pooling](https://docs.djangoproject.com/en/6.1/ref/databases/#transaction-pooling-and-server-side-cursors)
and [PgBouncer's configuration](https://www.pgbouncer.org/config.html).

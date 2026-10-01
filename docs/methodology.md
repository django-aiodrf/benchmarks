# Methodology

## A sample

A sample is one framework, server, worker count and scenario, measured on a
fresh server:

1. Reset the data the scenario writes (the SQL tables, the MongoDB and
   Elasticsearch write collections, the cache namespace) and seed it again.
2. Start the server, pinned to its CPUs, and wait until it answers.
3. Verify the scenario's contract: the exact response; for the authenticated
   workloads, a 401 without a valid token; for the service workloads, a 405
   for the wrong method; for writes, the persisted data.
4. Warm up for `--warmup` seconds, then reset the written data again.
5. Load the server with [oha](https://github.com/hatoo/oha): `--concurrency`
   HTTP/1.1 keep-alive connections, no compression, for `--duration` seconds;
   writes send a fixed number of requests (`--post-requests`) instead, so that
   every sample writes the same amount of data.
6. Verify the contract again, and the number of written rows or documents.
7. Stop the server's process group and check its log.

A sample is invalid if a verification fails, any response has an unexpected
status, oha reports an error, or the server log contains a traceback, a
`RuntimeWarning` or an unclosed client. Invalid samples are kept in the
results and listed in the report; a group with one is not ranked.

During the load the runner samples the server process tree's CPU and memory,
and reads each service container's CPU time (below).

## Order and repetitions

For each repetition and scenario, the order of the configurations is
shuffled with a recorded seed (`--seed`), so that slow drifts of the host do
not favour one framework. A group's result is the median over its
repetitions; the report's *spread* is half the range between the fastest and
slowest repetition, relative to the median. Two or three repetitions show
whether a difference is larger than the variation between starts; they are
not a confidence interval.

## CPU layout

The server, the load generator and the services must not compete for CPUs,
and a server with N workers gets exactly N CPUs. The default layout is for a
host with 8 performance cores (SMT, CPUs 0–15) and 12 efficiency cores
(CPUs 16–27), such as an Intel Core i7-14700:

| CPUs | Used by |
| --- | --- |
| 0, 2, 4, 6 | The server: its first N CPUs for N workers (`SERVER_CPUS`) |
| 1, 3, 5, 7 | Idle: the SMT siblings of the server CPUs |
| 8–15 | oha, with one thread per CPU (`LOAD_CPUS`) |
| 16–17 | PostgreSQL (`BENCH_PG_CPUS`) |
| 18–21 | Elasticsearch (`BENCH_ES_CPUS`) |
| 22–26 | MongoDB (`BENCH_MONGO_CPUS`) |
| 27 | Valkey (`BENCH_VALKEY_CPUS`), the HTTP fixture (`BENCH_HTTP_CPUS`) and PgBouncer when selected; no workload uses more than one of them |

On another host, give the server one physical core per worker for the largest
worker count, the load generator as many cores, and the services the rest; keep
the services' CPU variables in the environment when starting Compose. The
manifest records the layout of every run. Processes outside the benchmark
(a desktop session, for example) are not confined; run on a quiet host.

## Services must not limit the result

The services run with the settings in [infrastructure](infrastructure.md):
each on its own CPUs, with the per-request disk flush of writes disabled
(PostgreSQL `synchronous_commit=off`, the Elasticsearch write index's
asynchronous translog, MongoDB writes without journal acknowledgement), so
that write workloads measure the frameworks rather than the disk, and
identically for every framework.

Two workload settings keep the services' own work small without changing
what the frameworks do. MongoDB and Elasticsearch hold 200 documents
(`BENCH_DOCUMENTS`), enough for the reads' twenty results, and Elasticsearch
answers repeated searches and aggregations from its shard request cache. The
client call, the round trip and the response stay the same.

Before the first sample, the runner sends each selected MongoDB and
Elasticsearch workload 20,000 times directly to the service
(`service_warmup` in the manifest). Elasticsearch runs on the JVM, whose
compiler needs far more requests than one sample's warmup: without this
step, the first framework measured used up to twice the Elasticsearch CPU per
request of the frameworks measured after it.

During each sample, the runner reads every service container's CPU time
from its cgroup (`cpu.stat`) and records its utilization: the CPU time used,
divided by the sample's duration and by the number of CPUs the service has.
It records the load generator's CPU time the same way. The report lists the
busiest service of every group, the highest utilization per scenario and the
groups where oha used most of its CPUs, and marks 80% or more: at that level
the service or the load generator, rather than the framework, may have set
the throughput.

One group reaches that level on the default host: `mongo-native-aggregate`
with four workers. A MongoDB `$group` costs about 0.3 ms of server CPU per
request, whatever the collection size (measured with 20 to 200 documents,
with and without an index), so the fastest configurations use all of
MongoDB's CPUs. During calibration, Django Bolt's throughput rose with every
CPU given to MongoDB (about 9,300, 12,600 and 14,500 aggregations per second
with 4, 5 and 7 CPUs), and MongoDB stayed 96–99% busy each time. Read these
groups as MongoDB's throughput, not the framework's.

The HTTP fixture answers after a fixed 10 ms delay: the `http-*` and
`mixed-*` workloads measure how a framework overlaps those waits. Client pools
are large enough not to queue: 10 PostgreSQL connections, and 64 MongoDB,
Elasticsearch, Valkey and HTTP connections per worker process
([configuration](configuration.md)).

## The load

oha keeps `--concurrency` requests in flight (a closed loop): throughput and
latency are measured together, and latency percentiles are those of the
requests oha completed. It is not an open-loop, fixed-arrival-rate test, and
latencies are not corrected for coordinated omission. A request slower than 15
seconds counts as an error.

## Limits

- One host runs the servers, the load generator and the services. Results
  depend on the CPU, the kernel and the configuration; compare configurations
  within a run, not across runs on different hosts.
- The applications have no middleware, sessions, TLS or compression. The
  measurement includes the server and the framework, not a production stack.
- FastAPI and Litestar use the Django ORM, not their usual database layers.
- Django Bolt's server, routing and worker model differ from the ASGI and WSGI
  servers; its results include them.
- Gunicorn's sync worker closes the connection after each response; its
  results include the reconnections.
- The mixed workloads run pure-Python CPU work, which holds the GIL: a thread
  pool keeps the event loop responsive but does not add CPU throughput.

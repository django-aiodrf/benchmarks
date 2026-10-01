# Profiling

The HTTP runs say how fast a configuration is; the profiling tools say where
a framework spends its time. They run the application in-process, without an
HTTP server or load generator, so their numbers are not comparable with the
HTTP results. Run them on an otherwise idle host, never during a measurement.

## Per scenario

```sh
.venv/bin/python tools/profile_cases.py --framework aiodrf-tuned --reset-database \
  --scenarios json db articles --requests 40 --warmup 5 --profiler cprofile \
  --output results/profiles/aiodrf-tuned
```

`--framework` accepts every framework served by `bench.asgi` (all except
Bolt). The tool prepares the services, verifies each response, and writes per
scenario the raw profile, self-time and cumulative tables, and process CPU and
wall time, with an environment record.

- `cprofile` counts calls and finds hot functions. Its cumulative times
  overlap across coroutines and threads; do not add them up as CPU shares.
- `yappi` (with its CPU clock) separates worker-thread CPU from waiting.
- `pyinstrument` shows elapsed-time call paths of the event loop's thread.

Install the last two when needed:
`uv pip install --python .venv/bin/python pyinstrument yappi`.

## NoSQL paths

```sh
.venv/bin/python tools/profile_nosql.py --framework ninja \
  --scenario mongo-native-read --requests 200 --output results/profiles/mongo-native
```

## Rendering and instruction counts

`tools/profile_rendering.py` times aiodrf's serializer output paths without a
database; with `valgrind --tool=cachegrind` and the markers of
`tools/profile_markers.c` it counts the instructions of the measured
iterations only:

```sh
.venv/bin/python tools/profile_rendering.py --case compiled-articles \
  --iterations 5000 --repeats 7 --output results/cpu/compiled-articles.json
cc -shared -fPIC tools/profile_markers.c -o results/cpu/profile-markers.so
valgrind --tool=cachegrind --instr-at-start=no --cache-sim=no --branch-sim=no \
  --cachegrind-out-file=results/cpu/compiled-articles.cachegrind \
  .venv/bin/python tools/profile_rendering.py --case compiled-articles \
  --iterations 200 --repeats 1 --markers results/cpu/profile-markers.so \
  --output results/cpu/compiled-articles-instructions.json
```

"""Profile one ASGI read path without mixing instrumentation into HTTP timings.

Run from the benchmark root with its virtualenv. CPython 3.12+ cProfile uses
interpreter-wide monitoring and these profiles include worker functions.
Tables do not separate threads; cumulative time includes overlapping waits.
Process CPU time includes all threads. This is not a throughput measurement.
"""

import argparse
import cProfile
import json
import os
import pstats
import sys
import time
from pathlib import Path

import httpx
import uvloop

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def profile_case(args):
    from bench.asgi import application
    from bench.profiles import PATHS
    from bench.services import ServiceConfig, expected_service, resources
    from bench.snapshot import snapshot

    expected = expected_service(
        args.scenario, args.dataset_size, ServiceConfig.from_env()
    )
    async with resources() as clients:

        async def with_state(scope, receive, send):
            scope = {**scope, "state": {"resources": clients}}
            await application(scope, receive, send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=with_state), base_url="http://127.0.0.1"
        ) as client:
            path = PATHS[args.scenario]
            for _ in range(20):
                response = await client.get(path)
                assert response.status_code == 200, response.text
                assert response.json() == expected
            profiler = cProfile.Profile()
            cpu_start = time.process_time()
            wall_start = time.perf_counter()
            profiler.enable()
            try:
                for _ in range(args.requests):
                    response = await client.get(path)
                    assert response.status_code == 200, response.text
            finally:
                profiler.disable()
            summary = {
                "profile": args.framework,
                "scenario": args.scenario,
                "requests": args.requests,
                "wall_seconds": time.perf_counter() - wall_start,
                "process_cpu_seconds": time.process_time() - cpu_start,
                "scope": "in-process sequential ASGI; cProfile interpreter events, not per-thread CPU attribution",
            }
    profiler.dump_stats(args.output / "profile.pstats")
    with (args.output / "cumulative.txt").open("w") as output:
        pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats(60)
    with (args.output / "self-time.txt").open("w") as output:
        pstats.Stats(profiler, stream=output).sort_stats("tottime").print_stats(60)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.output / "environment.json").write_text(
        json.dumps(snapshot(), indent=2) + "\n"
    )
    print(json.dumps(summary))


def main():
    from bench.profiles import PROFILES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--framework",
        # Every framework served by bench.asgi; Bolt has its own server.
        choices=[profile for profile in PROFILES if profile != "bolt"],
        required=True,
    )
    parser.add_argument(
        "--scenario",
        choices=[
            "mongo-read",
            "mongo-native-read",
            "elasticsearch-search",
            "elasticsearch-native-search",
        ],
        required=True,
    )
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--dataset-size", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.requests <= 10000:
        parser.error("--requests must be between 1 and 10000")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["BENCH_PROFILE"] = args.framework
    os.environ["DJANGO_SETTINGS_MODULE"] = "bench.settings"
    uvloop.run(profile_case(args))


if __name__ == "__main__":
    main()

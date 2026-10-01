"""Profile validated ASGI workloads separately from the HTTP throughput runs.

Run this only against the isolated benchmark services, with no load run active.
Each case warms up, resets its write/cache state, then profiles sequential
requests. cProfile includes interpreter events; cumulative waits overlap.
Yappi's CPU clock separates worker CPU from waiting. Neither is an RPS test.
"""

import argparse
import asyncio
import contextlib
import cProfile
import json
import os
import pstats
import sys
import time
from pathlib import Path

import uvloop

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def request(application, clients, scenario):
    from bench.profiles import CREATE_BODY, PATHS, method_for, status_for

    body = json.dumps(CREATE_BODY).encode() if scenario == "article-create" else b""
    path = PATHS[scenario]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method_for(scenario),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"127.0.0.1"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 50000),
        "state": {"resources": clients},
    }
    sent = False
    messages = []

    async def receive():
        nonlocal sent
        if sent:
            await asyncio.Event().wait()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await application(scope, receive, send)
    content = b"".join(message.get("body", b"") for message in messages)
    if not messages or messages[0].get("status") != status_for(scenario):
        raise ValueError(f"{scenario}: unexpected ASGI response: {messages!r}")
    return content


def verify_content(scenario, content, size):
    from bench.fixtures import author_values, fixture_article, tag_values
    from bench.profiles import CREATE_BODY, JSON_10K_BODY, JSON_BODY
    from bench.services import ServiceConfig, expected_service

    actual = json.loads(content)
    if scenario == "article-create":
        if type(actual.get("id")) is not int or actual["id"] <= size:
            raise ValueError("Create did not return a new primary key")
        expected = {
            "id": actual["id"],
            "title": CREATE_BODY["title"],
            "body": CREATE_BODY["body"],
            "author": author_values(1),
            "tags": [tag_values(1), tag_values(2)],
        }
    else:
        core = {
            "json": JSON_BODY,
            "json-10k": JSON_10K_BODY,
            "db": [author_values(i) for i in range(1, 11)],
            "articles": {
                "total": size,
                "items": [fixture_article(i) for i in range(1, 21)],
            },
            "article-detail": fixture_article(1),
        }
        expected = (
            core[scenario]
            if scenario in core
            else expected_service(scenario, size, ServiceConfig.from_env())
        )
    if actual != expected:
        raise ValueError(f"{scenario}: response differs from fixture contract")


def prepare(scenario):
    from django.core.management import call_command

    from bench.infrastructure import prepare_case

    if scenario == "article-create":
        call_command("seed_benchmark", reset=True, verbosity=0)
    prepare_case(scenario)


def verify_storage(scenario, requests, size):
    from bench.app.models import Article
    from bench.infrastructure import verify_writes
    from bench.profiles import SERVICE_WRITES

    if scenario == "article-create" and Article.objects.count() != size + requests:
        raise ValueError("Persisted article count differs from successful requests")
    if scenario in SERVICE_WRITES:
        verify_writes(scenario, requests)


@contextlib.contextmanager
def instrument(mode, output):
    if mode == "cprofile":
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            yield
        finally:
            profiler.disable()
            profiler.dump_stats(output / "profile.pstats")
            for name, key in (("self-time", "tottime"), ("cumulative", "cumulative")):
                with (output / f"{name}.txt").open("w") as stream:
                    pstats.Stats(profiler, stream=stream).sort_stats(key).print_stats(
                        100
                    )
    elif mode == "pyinstrument":
        from pyinstrument import Profiler

        profiler = Profiler(interval=0.0005, async_mode="enabled")
        profiler.start()
        try:
            yield
        finally:
            profiler.stop()
            (output / "profile.html").write_text(profiler.output_html())
            (output / "profile.txt").write_text(
                profiler.output_text(unicode=True, color=False, show_all=True)
            )
    elif mode == "yappi":
        import yappi

        yappi.clear_stats()
        yappi.set_clock_type("cpu")
        yappi.start(builtins=True)
        try:
            yield
        finally:
            yappi.stop()
            yappi.get_func_stats().save(str(output / "profile.pstats"), type="pstat")
            with (output / "functions.txt").open("w") as stream:
                yappi.get_func_stats().sort("ttot").print_all(out=stream)
            with (output / "threads.txt").open("w") as stream:
                yappi.get_thread_stats().print_all(out=stream)
    else:
        raise ValueError(f"Unknown profiler: {mode}")


async def run(args):
    from asgiref.sync import sync_to_async

    from bench.asgi import application
    from bench.services import resources
    from bench.snapshot import snapshot

    (args.output / "environment.json").write_text(
        json.dumps(snapshot(), indent=2) + "\n"
    )
    async with resources() as clients:
        for scenario in args.scenarios:
            output = args.output / scenario
            output.mkdir()
            await sync_to_async(prepare)(scenario)
            for _ in range(args.warmup):
                content = await request(application, clients, scenario)
                verify_content(scenario, content, args.dataset_size)
            await sync_to_async(prepare)(scenario)
            with instrument(args.profiler, output):
                cpu_start = time.process_time()
                wall_start = time.perf_counter()
                for _ in range(args.requests):
                    content = await request(application, clients, scenario)
                cpu = time.process_time() - cpu_start
                wall = time.perf_counter() - wall_start
            verify_content(scenario, content, args.dataset_size)
            await sync_to_async(verify_storage)(
                scenario, args.requests, args.dataset_size
            )
            summary = {
                "framework": args.framework,
                "scenario": scenario,
                "profiler": args.profiler,
                "requests": args.requests,
                "warmup": args.warmup,
                "process_cpu_seconds": cpu,
                "wall_seconds": wall,
                "contract": "passed",
                "scope": "sequential in-process ASGI, no HTTP client or server; instrumented, not throughput",
            }
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary), flush=True)
            # Subsequent article/cache reads must see the original dataset.
            if scenario == "article-create":
                await sync_to_async(prepare)(scenario)


def prepare_inputs(args):
    """Seed selected stores outside profiling; never rely on a previous run."""
    from bench.infrastructure import prepare_services
    from bench.processes import seed
    from bench.profiles import required_services

    services = required_services(args.scenarios)
    if "postgres" in services:
        seed(reset=True, migrate=True)
    prepare_services(services, size=args.dataset_size, reset=True)


def main():
    from bench.profiles import PROFILES, SCENARIOS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--framework",
        # Every framework served by bench.asgi; Bolt has its own server.
        choices=[profile for profile in PROFILES if profile != "bolt"],
        required=True,
    )
    parser.add_argument("--scenarios", choices=SCENARIOS, nargs="+", default=SCENARIOS)
    parser.add_argument(
        "--profiler", choices=["cprofile", "pyinstrument", "yappi"], default="cprofile"
    )
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--dataset-size", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reset-database", action="store_true", required=True)
    args = parser.parse_args()
    if not 1 <= args.requests <= 10000 or not 1 <= args.warmup <= 100:
        parser.error("Use 1..10000 requests and 1..100 warmup requests")
    if not 20 <= args.dataset_size <= 100000:
        parser.error("Dataset size must be between 20 and 100000")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        BENCH_PROFILE=args.framework,
        DJANGO_SETTINGS_MODULE="bench.settings",
        BENCH_ALLOW_RESET="1",
        BENCH_DATASET_SIZE=str(args.dataset_size),
    )
    prepare_inputs(args)
    uvloop.run(run(args))


if __name__ == "__main__":
    main()

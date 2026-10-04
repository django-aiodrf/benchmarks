"""Isolate response CPU work with repeated timings or Cachegrind markers.

Use PYTHONPATH to select an archived source tree for baseline comparisons.
These measurements exclude Django dispatch, service I/O, and server overhead.
"""

import argparse
import ctypes
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")


def operation(case):
    import django

    django.setup()
    from aiodrf.response import Response, _plain_data
    from aiodrf.utils import awaits_inline, resolve_pair
    from aiodrf.views import APIView
    from rest_framework.renderers import JSONRenderer

    from bench.fixtures import fixture_article
    from bench.profiles import JSON_BODY

    if case.startswith("classify-"):
        import functools

        view = APIView()
        targets = {
            "classify-function": APIView.ainitial,
            "classify-method": view.ainitial,
            "classify-sync": view.initialize_request,
            "classify-partial": functools.partial(view.ainitial),
        }
        target = targets[case]
        return lambda: awaits_inline(target)

    if case == "compiled-articles":
        from types import SimpleNamespace

        from fastdrf.compiler import OutputField, OutputSpec
        from fastdrf.msgspec.compiler import build

        named = OutputSpec(
            "Named",
            [
                OutputField(name, name, kind, False)
                for name, kind in (("id", int), ("name", str))
            ],
        )
        article = OutputSpec(
            "Article",
            [
                OutputField(name, name, kind, False)
                for name, kind in (("id", int), ("title", str), ("body", str))
            ]
            + [
                OutputField("author", "author", named, False),
                OutputField("tags", "tags", named, False, many=True),
            ],
        )
        rows = []
        for i in range(1, 21):
            value = fixture_article(i)
            value["author"] = SimpleNamespace(**value["author"])
            value["tags"] = [SimpleNamespace(**tag) for tag in value["tags"]]
            rows.append(SimpleNamespace(**value))
        encoder = build(article)
        return lambda: encoder.dump_many(rows)

    payload = (
        {"total": 1000, "items": [fixture_article(i) for i in range(1, 21)]}
        if case.endswith("articles")
        else JSON_BODY
    )
    renderer = JSONRenderer()

    def response():
        result = Response(payload)
        result.accepted_renderer = renderer
        result.accepted_media_type = "application/json"
        result.renderer_context = {}
        return result

    if case.startswith("scan-"):
        return lambda: _plain_data(payload)
    if case == "render-lookup":
        result = response()
        return lambda: result.render
    if case == "resolve-pair":
        view = APIView()
        return lambda: resolve_pair(view, "check_permissions", "acheck_permissions")
    return lambda: response().render()


def main():
    from bench.snapshot import snapshot

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=[
            "scan-json",
            "scan-articles",
            "render-json",
            "render-articles",
            "render-lookup",
            "resolve-pair",
            "compiled-articles",
            "classify-function",
            "classify-method",
            "classify-sync",
            "classify-partial",
        ],
        required=True,
    )
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--markers", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.repeats < 1:
        parser.error("Iteration and repetition counts must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("Output already exists")
    run = operation(args.case)
    for _ in range(100):
        run()
    markers = ctypes.CDLL(str(args.markers.resolve())) if args.markers else None
    samples = []
    for _ in range(args.repeats):
        if markers:
            markers.profile_start()
        start = time.process_time_ns()
        try:
            for _ in range(args.iterations):
                run()
        finally:
            elapsed = time.process_time_ns() - start
            if markers:
                markers.profile_stop()
        samples.append(elapsed / args.iterations)
    result = {
        "case": args.case,
        "iterations": args.iterations,
        "repeats": args.repeats,
        "instrumented": bool(markers),
        "cpu_ns_per_operation": samples,
        "median_cpu_ns": statistics.median(samples),
        "environment": snapshot(),
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{args.case}: {result['median_cpu_ns']:.0f} CPU ns/operation", flush=True)


if __name__ == "__main__":
    main()

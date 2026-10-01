"""Command-line entry points for setup, verification and measurements."""

import argparse
import os
import signal
import subprocess
from pathlib import Path

from bench.processes import ROOT, environment, seed, server_command
from bench.profiles import PROFILES, SCENARIOS, required_services
from bench.results import write_report
from bench.servers import SERVERS
from bench.verify import verify


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def cpu_list(value: str) -> list[int]:
    """``0,2,4,6`` or ``8-11``: logical CPU numbers, in order."""
    cpus = []
    for part in value.split(","):
        first, _, last = part.partition("-")
        cpus += range(int(first), int(last or first) + 1)
    if not cpus or len(cpus) != len(set(cpus)) or min(cpus) < 0:
        raise argparse.ArgumentTypeError("expected distinct CPU numbers")
    return cpus


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="Migrate and seed only the benchmark database"
    )
    prepare.add_argument("--reset-database", action="store_true")
    prepare.add_argument(
        "--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS)
    )
    serve = commands.add_parser("serve", help="Run one framework for manual checks")
    serve.add_argument("--framework", choices=PROFILES, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=positive, default=8100)
    serve.add_argument("--workers", type=positive, default=1)
    serve.add_argument("--server", choices=("default", *SERVERS), default="default")
    check = commands.add_parser(
        "verify", help="Check HTTP contracts, including selected write workloads"
    )
    check.add_argument("--url", default="http://127.0.0.1:8100")
    check.add_argument(
        "--dataset-size",
        type=positive,
        default=int(os.environ.get("BENCH_DATASET_SIZE", "1000")),
    )
    check.add_argument("--read-only", action="store_true")
    check.add_argument(
        "--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS)
    )
    run = commands.add_parser(
        "run", help="Measure all selected cases with fresh server starts"
    )
    run.add_argument("--reset-database", action="store_true")
    run.add_argument(
        "--frameworks", nargs="+", choices=PROFILES, default=list(PROFILES)
    )
    run.add_argument(
        "--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS)
    )
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=positive, default=8100)
    run.add_argument("--workers", type=positive, nargs="+", default=[1, 4])
    run.add_argument(
        "--servers",
        choices=("all", "default", *SERVERS),
        nargs="+",
        default=["all"],
        help="all: every server each framework runs on; default: its first one",
    )
    run.add_argument("--mode", choices=("baremetal", "docker"), default="baremetal")
    run.add_argument("--image", default="aiodrf-benchmarks:local")
    run.add_argument(
        "--cpus",
        type=float,
        default=4,
        help="Docker CPU quota; fixed across worker counts",
    )
    run.add_argument("--memory", default="2g", help="Docker application memory limit")
    run.add_argument(
        "--duration", type=positive, default=10, help="Seconds per read sample"
    )
    run.add_argument(
        "--warmup", type=int, default=2, help="Seconds per endpoint warmup; 0 disables"
    )
    run.add_argument("--repeats", type=positive, default=3)
    run.add_argument("--concurrency", type=positive, default=64)
    run.add_argument("--post-requests", type=positive, default=3000)
    run.add_argument("--load-threads", type=positive, default=2)
    run.add_argument(
        "--server-cpus",
        type=cpu_list,
        help="Pin a server of N workers to the first N of these CPUs (taskset)",
    )
    run.add_argument(
        "--load-cpus",
        type=cpu_list,
        help="Pin oha to these CPUs, with one load thread per CPU",
    )
    run.add_argument(
        "--case-timeout",
        type=positive,
        default=600,
        help="Maximum seconds for a POST sample",
    )
    run.add_argument(
        "--seed", type=int, default=42, help="Profile-order randomization seed"
    )
    run.add_argument("--oha", default="oha")
    report = commands.add_parser("report", help="Rebuild CSV and Markdown from one run")
    report.add_argument("directory", type=Path)
    merge = commands.add_parser(
        "merge",
        help="Replace the scenarios a later run measured again, and report the result",
    )
    merge.add_argument("base", type=Path, help="The run to correct")
    merge.add_argument("correction", type=Path, help="The run that measured them again")
    merge.add_argument("--output", type=Path, required=True, help="A new directory")
    merge.add_argument(
        "--reason", required=True, help="Why the first samples were replaced"
    )
    return result


def stop_on_sigterm() -> None:
    """Exit on SIGTERM through the normal unwinding, as on Ctrl-C.

    Python's default SIGTERM action ends the process without running
    ``finally`` blocks, which would leave the server under test running.
    """

    def stop(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)


def main() -> None:
    arguments = parser()
    args = arguments.parse_args()
    stop_on_sigterm()
    try:
        if args.command == "prepare":
            from bench.infrastructure import prepare_services

            services = required_services(args.scenarios)
            if "postgres" in services:
                seed(reset=args.reset_database, migrate=True)
            prepare_services(
                services,
                size=int(os.environ.get("BENCH_DATASET_SIZE", "1000")),
                reset=args.reset_database,
            )
            print("Benchmark dataset is ready.")
        elif args.command == "serve":
            os.chdir(ROOT)
            command = server_command(
                args.framework, args.host, args.port, args.workers, args.server
            )
            os.execve(command[0], command, environment(args.framework))
        elif args.command == "verify":
            print(
                verify(
                    args.url,
                    dataset_size=args.dataset_size,
                    write=not args.read_only,
                    scenarios=args.scenarios,
                )
            )
        elif args.command == "report":
            write_report(args.directory)
        elif args.command == "merge":
            from bench.results import merge

            merge(args.base, args.correction, args.output, reason=args.reason)
            write_report(args.output)
        elif args.command == "run":
            from bench.runner import run

            if args.warmup < 0:
                raise ValueError("--warmup cannot be negative")
            if args.cpus <= 0 or len(args.workers) != len(set(args.workers)):
                raise ValueError("CPU quota must be positive and worker counts unique")
            if len(args.frameworks) != len(set(args.frameworks)) or len(
                args.scenarios
            ) != len(set(args.scenarios)):
                raise ValueError("Frameworks and scenarios cannot contain duplicates")
            run(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        arguments.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()

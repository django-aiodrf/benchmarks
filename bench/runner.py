"""The measurement loop: every configuration and scenario, one sample at a time."""

import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg

from bench.auth import token
from bench.infrastructure import (
    image_records,
    prepare_case,
    prepare_services,
    setup_django,
    verify_writes,
    warm_services,
)
from bench.monitor import ResourceMonitor
from bench.processes import (
    ROOT,
    container_server,
    environment,
    pinned,
    seed,
    server,
    server_command,
)
from bench.profiles import (
    AUTH_SCENARIOS,
    CREATE_BODY,
    PATHS,
    SERVICE_WRITES,
    WRITE_SCENARIOS,
    required_services,
    status_for,
)
from bench.results import normalize, write_report
from bench.servers import server_matrix
from bench.service_usage import ServiceUsage, containers
from bench.services import ServiceConfig
from bench.snapshot import snapshot
from bench.verify import verify


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def profile_settings(profile: str) -> dict:
    code = """
import json
import django
django.setup()
from django.conf import settings
from fastdrf.settings import fastdrf_settings
print(json.dumps({
    "REST_FRAMEWORK": settings.REST_FRAMEWORK,
    "AIODRF": settings.AIODRF,
    "FASTDRF": settings.FASTDRF,
    "serializer_backend": fastdrf_settings.SERIALIZER_BACKEND,
}))
"""
    result = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", code], env=environment(profile), text=True
        )
    )
    result["orm"] = (
        "SQLAlchemy async (psycopg)"
        if profile in ("fastapi", "litestar")
        else "Django ORM (psycopg)"
    )
    return result


def provenance(args, oha: str) -> dict:
    setup_django()
    from django.conf import settings

    database = {}
    pooler = {}
    if "postgres" in required_services(args.scenarios):
        with psycopg.connect(
            dbname="aiodrf_benchmarks",
            host=os.environ.get("BENCH_PG_HOST", "127.0.0.1"),
            port=settings.DATABASES["default"]["PORT"],
            user=os.environ.get("BENCH_PG_USER", "bench"),
            password=os.environ.get("BENCH_PG_PASSWORD", "benchmark-local-only"),
            connect_timeout=5,
        ) as connection:
            for name in (
                "server_version",
                "max_connections",
                "shared_buffers",
                "effective_cache_size",
                "work_mem",
                "fsync",
                "synchronous_commit",
                "io_method",
                "io_workers",
                "effective_io_concurrency",
                "maintenance_io_concurrency",
                "random_page_cost",
                "max_wal_size",
            ):
                database[name] = connection.execute(f"SHOW {name}").fetchone()[0]
        database["route"] = "pgbouncer" if settings.BENCH_PGBOUNCER else "direct"
        database["application_pool"] = settings.DATABASES["default"]["OPTIONS"]["pool"]
        if settings.BENCH_PGBOUNCER:
            with psycopg.connect(
                dbname="pgbouncer",
                host=settings.DATABASES["default"]["HOST"],
                port=settings.DATABASES["default"]["PORT"],
                user=settings.DATABASES["default"]["USER"],
                password=settings.DATABASES["default"]["PASSWORD"],
                connect_timeout=5,
                autocommit=True,
            ) as admin:
                pooler["version"] = admin.execute("SHOW VERSION").fetchone()[0]
                pooler["config"] = {
                    row[0]: row[1]
                    for row in admin.execute("SHOW CONFIG")
                    if row[0]
                    in (
                        "pool_mode",
                        "default_pool_size",
                        "min_pool_size",
                        "reserve_pool_size",
                        "max_client_conn",
                        "max_db_connections",
                        "max_prepared_statements",
                    )
                }
    environment = snapshot()
    parameters = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(args).items()
    }
    config = ServiceConfig.from_env()
    record = {
        "schema_version": 5,
        "state": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "parameters": parameters,
        "runner_environment": environment,
        "server_environment": environment,
        "database": database,
        "pgbouncer": pooler,
        "profile_settings": {
            profile: profile_settings(profile) for profile in args.frameworks
        },
        "service_clients_enabled": os.environ.get("BENCH_SERVICE_CLIENTS", "1") == "1",
        "sqlalchemy_pool": {
            "pool_size": settings.BENCH_PG_POOL_MAX,
            "max_overflow": 0,
            "pool_timeout": 10,
        },
        "dataset_size": int(os.environ.get("BENCH_DATASET_SIZE", "1000")),
        "oha_version": subprocess.check_output([oha, "--version"], text=True).strip(),
        "workload": {
            "cpu_rounds": config.cpu_rounds,
            "cpu_threads": config.cpu_threads,
            "client_pool_per_worker": config.pool_size,
            "http_client_lifetime": "worker; http-no-reuse creates a client per request",
            "cache_backend": settings.CACHES["default"]["BACKEND"],
            "async_cache_backend": settings.CACHES["async"]["BACKEND"],
            "mongo_backend": "django_mongodb_backend",
            "mongo_native": "pymongo.AsyncMongoClient (sync profiles: MongoClient)",
            "search_backend": "django_elasticsearch_dsl",
            "search_native": "elasticsearch.dsl.AsyncSearch/async_bulk (sync profiles: Search/bulk)",
            "bolt_service_loop": "submit to lifespan loop to preserve client affinity; included in timing",
        },
        "server_commands": {
            f"{profile}-{server_name}-w{workers}": server_command(
                profile, args.host, args.port, workers, server_name
            )
            for profile, server_name, workers in server_matrix(
                args.frameworks, args.servers, args.workers
            )
        },
    }
    if args.mode == "docker":
        image = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", args.image], text=True, timeout=15
            )
        )[0]
        record["image"] = {
            "id": image["Id"],
            "repo_digests": image.get("RepoDigests", []),
        }
        # Resolve once to an immutable image ID for every server start in this run.
        args.image = image["Id"]
        record["server_environment"] = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    args.image,
                    "python",
                    "-m",
                    "bench.snapshot",
                ],
                text=True,
                timeout=30,
            )
        )
        if (
            record["server_environment"]["sources"]["benchmark"]["python_sha256"]
            != environment["sources"]["benchmark"]["python_sha256"]
        ):
            raise ValueError(
                "Container benchmark source differs from the runner; rebuild the image"
            )
    return record


def load_command(oha: str, args, scenario: str, *, warmup: bool = False) -> list[str]:
    command = [
        oha,
        "--no-tui",
        "--output-format",
        "json",
        "--disable-compression",
        "--http-version",
        "1.1",
        "--worker-threads",
        str(len(args.load_cpus) if args.load_cpus else args.load_threads),
        "-c",
        str(args.concurrency),
        "-t",
        "15s",
        "-w",
    ]
    if scenario in WRITE_SCENARIOS:
        command += [
            "-m",
            "POST",
            "-T",
            "application/json",
            "-d",
            json.dumps(
                CREATE_BODY if scenario == "article-create" else {},
                separators=(",", ":"),
            ),
        ]
    if scenario in AUTH_SCENARIOS:
        command += ["-H", f"Authorization: Bearer {token()}"]
    if scenario in WRITE_SCENARIOS and not warmup:
        command += ["-n", str(args.post_requests)]
    else:
        command += ["-z", f"{args.warmup if warmup else args.duration}s"]
    command += [f"http://{args.host}:{args.port}{PATHS[scenario]}"]
    return pinned(command, args.load_cpus, len(args.load_cpus or ()))


def load(command: list[str], destination: Path, *, timeout: float) -> tuple[dict, dict]:
    """
    Run the load generator; return its result and its CPU use: seconds of CPU
    time, and cores (CPU seconds per elapsed second).
    """
    write_json(destination.with_suffix(".command.json"), command)
    with (
        destination.open("w") as output,
        destination.with_suffix(".stderr.log").open("w") as errors,
    ):
        started = time.monotonic()
        child = subprocess.Popen(command, stdout=output, stderr=errors)
        deadline = started + timeout
        while True:
            pid, status, rusage = os.wait4(child.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() > deadline:
                child.kill()
                child.wait()
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(0.05)
        elapsed = time.monotonic() - started
        child.returncode = os.waitstatus_to_exitcode(status)
    if child.returncode:
        raise subprocess.CalledProcessError(child.returncode, command)
    cpu = rusage.ru_utime + rusage.ru_stime
    return json.loads(destination.read_text()), {
        "cpu_seconds": round(cpu, 3),
        "cores": round(cpu / elapsed, 3) if elapsed else 0.0,
    }


def measure(
    args,
    oha: str,
    scenario: str,
    profile: str,
    workers: int,
    case: Path,
    size: int,
    server_name: str = "default",
) -> dict:
    sql = "postgres" in required_services([scenario])
    if sql:
        seed(reset=True)
    prepare_case(scenario)
    kwargs = {
        "host": args.host,
        "port": args.port,
        "workers": workers,
        "log": case / "server.log",
        "server_name": server_name,
    }
    context = (
        container_server(
            profile, **kwargs, image=args.image, cpus=args.cpus, memory=args.memory
        )
        if args.mode == "docker"
        else server(profile, **kwargs, cpus=args.server_cpus)
    )
    with context as process:
        base_url = f"http://{args.host}:{args.port}"
        write_json(
            case / "verification.json",
            verify(base_url, dataset_size=size, scenarios=[scenario]),
        )
        if scenario in SERVICE_WRITES:
            verify_writes(scenario, 1)
        if args.warmup:
            raw, _ = load(
                load_command(oha, args, scenario, warmup=True),
                case / "warmup.json",
                timeout=args.warmup + 60,
            )
            normalize(raw, status=status_for(scenario))
        if sql:
            seed(reset=True)
        prepare_case(scenario)
        fixture_before = None
        if "http" in required_services([scenario]):
            with httpx.Client(timeout=5, trust_env=False) as observer:
                response = observer.get(ServiceConfig.from_env().http_url + "/health")
                response.raise_for_status()
                fixture_before = response.json()
        monitor = ResourceMonitor(process if isinstance(process, int) else process.pid)
        usage = ServiceUsage(containers())
        try:
            with monitor, usage:
                raw, generator = load(
                    load_command(oha, args, scenario),
                    case / "oha.json",
                    timeout=args.case_timeout
                    if scenario in WRITE_SCENARIOS
                    else args.duration + 60,
                )
        finally:
            write_json(
                case / "resources.json",
                {"samples": monitor.samples, **monitor.summary()},
            )
        result = normalize(
            raw,
            status=status_for(scenario),
            expected_requests=args.post_requests
            if scenario in WRITE_SCENARIOS
            else None,
        )
        if fixture_before is not None:
            with httpx.Client(timeout=5, trust_env=False) as observer:
                response = observer.get(ServiceConfig.from_env().http_url + "/health")
                response.raise_for_status()
                after = response.json()
            # Health checks can add connections; these are diagnostic raw counters.
            write_json(
                case / "http-connections.json",
                {"before": fixture_before, "after": after},
            )
        total = size + (args.post_requests if scenario == "article-create" else 0)
        verify(base_url, dataset_size=total, write=False, scenarios=[scenario])
        if scenario in SERVICE_WRITES:
            verify_writes(scenario, args.post_requests)
        if scenario == "article-create":
            with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
                last = client.get(f"/articles/{total}")
                last.raise_for_status()
                if last.json()["title"] != CREATE_BODY["title"]:
                    raise ValueError(
                        "Final created article differs from the write payload"
                    )
        cpus = len(args.load_cpus) if args.load_cpus else args.load_threads
        generator["cpus"] = cpus
        generator["utilization"] = round(generator["cores"] / cpus, 3)
        return {
            **result,
            **monitor.summary(),
            "service_cpu": usage.result,
            "load_cpu": generator,
        }


def run(args) -> None:
    if not args.reset_database:
        raise ValueError(
            "run requires --reset-database; use only dedicated benchmark namespaces"
        )
    if os.environ.get("BENCH_DATABASE", "postgres") != "postgres":
        raise ValueError(
            "Measured runs require PostgreSQL configuration; SQLite is for functional checks only"
        )
    if args.host != "127.0.0.1":
        raise ValueError("The automated runner binds only to 127.0.0.1")
    combinations = server_matrix(args.frameworks, args.servers, args.workers)
    oha = shutil.which(args.oha)
    if oha is None:
        raise ValueError("Install oha or pass --oha /path/to/oha")
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    # Keep a failure manifest even if infrastructure is unavailable during setup.
    manifest = {
        "schema_version": 5,
        "state": "initializing",
        "parameters": {
            "repeats": args.repeats,
            "frameworks": args.frameworks,
            "scenarios": args.scenarios,
            "workers": args.workers,
            "servers": args.servers,
            "mode": args.mode,
        },
    }
    samples = []
    failed = False
    write_json(directory / "samples.json", samples)
    try:
        manifest = provenance(args, oha)
        write_json(directory / "manifest.json", manifest)
        size = manifest["dataset_size"]
        if not 20 <= size <= 100_000:
            raise ValueError("BENCH_DATASET_SIZE must be between 20 and 100000")
        services = required_services(args.scenarios)
        if "postgres" in services:
            seed(reset=True, migrate=True)
        manifest["services"] = prepare_services(services, size=size, reset=True)
        manifest["service_warmup"] = warm_services(args.scenarios)
        manifest["infrastructure_images"] = image_records()
        # Save exact Compose text even when services were provisioned separately.
        (directory / "compose.yaml").write_text((ROOT / "compose.yaml").read_text())
        shutil.copytree(ROOT / "infra", directory / "infra")
        write_json(directory / "manifest.json", manifest)
        randomizer = random.Random(args.seed)
        for repeat in range(1, args.repeats + 1):
            for scenario in args.scenarios:
                profiles = combinations.copy()
                randomizer.shuffle(profiles)
                for profile, server_name, workers in profiles:
                    case = (
                        directory
                        / f"{repeat:02d}-{scenario}-{profile}-{server_name}-w{workers}"
                    )
                    case.mkdir()
                    record = {
                        "repeat": repeat,
                        "scenario": scenario,
                        "profile": profile,
                        "workers": workers,
                        "server": server_name,
                        "valid": False,
                    }
                    print(
                        f"[{repeat}/{args.repeats}] {scenario}: {profile} / {server_name} / {workers} workers",
                        flush=True,
                    )
                    try:
                        record.update(
                            measure(
                                args,
                                oha,
                                scenario,
                                profile,
                                workers,
                                case,
                                size,
                                server_name,
                            ),
                            valid=True,
                        )
                    except (Exception, KeyboardInterrupt) as exc:
                        record["error"] = f"{type(exc).__name__}: {exc}"
                        failed = True
                        if isinstance(exc, KeyboardInterrupt):
                            raise
                    finally:
                        samples.append(record)
                        write_json(directory / "samples.json", samples)
        manifest["state"] = "failed" if failed else "complete"
    except BaseException as exc:
        manifest.update(state="interrupted", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        write_json(directory / "manifest.json", manifest)
        write_report(directory)
    if failed:
        raise ValueError(
            f"One or more samples failed; see {directory / 'samples.json'}"
        )

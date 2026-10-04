"""Validate samples, aggregate them per group and write the report."""

import csv
import json
import math
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

from bench.servers import resolve_server, server_matrix


def normalize(raw: dict, *, status: int, expected_requests: int | None = None) -> dict:
    summary, counts = raw["summary"], raw["statusCodeDistribution"]
    successful = counts.get(str(status), 0)
    if (
        raw.get("errorDistribution")
        or set(counts) != {str(status)}
        or successful <= 0
        or summary["successRate"] != 1
    ):
        raise ValueError(
            "Load sample includes errors, unexpected status codes or no successful requests"
        )
    if expected_requests is not None and successful != expected_requests:
        raise ValueError("Completed POST count differs from the fixed write volume")
    percentiles = raw["latencyPercentiles"]
    result = {
        "requests": successful,
        "rps": summary["requestsPerSec"],
        "elapsed_seconds": summary["total"],
        "p50_ms": percentiles["p50"] * 1000,
        "p95_ms": percentiles["p95"] * 1000,
        "p99_ms": percentiles["p99"] * 1000,
    }
    if any(not math.isfinite(value) or value < 0 for value in result.values()):
        raise ValueError("Load sample contains non-finite or negative measurements")
    return result


def busiest_service(samples: list[dict]) -> tuple[str, float | str]:
    """
    The service with the highest median CPU utilization over ``samples``
    (CPU used / CPUs allotted), and that utilization; ``("", "")`` without
    service measurements. Near 1.0, the service limited the throughput.
    """
    per_service: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        for name, usage in (sample.get("service_cpu") or {}).items():
            per_service[name].append(usage["utilization"])
    if not per_service:
        return "", ""
    name, values = max(per_service.items(), key=lambda item: statistics.median(item[1]))
    return name, round(statistics.median(values), 3)


def aggregate(directory: Path) -> tuple[dict, list[dict]]:
    records = json.loads((directory / "samples.json").read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    groups = defaultdict(list)
    workers = manifest["parameters"].get("workers", [1])
    workers = [workers] if isinstance(workers, int) else workers
    for scenario in manifest["parameters"]["scenarios"]:
        for profile, server, count in server_matrix(
            manifest["parameters"]["frameworks"],
            manifest["parameters"].get("servers", ["default"]),
            workers,
        ):
            groups[scenario, profile, server, count] = []
    for record in records:
        groups[
            record["scenario"],
            record["profile"],
            record.get("server", resolve_server(record["profile"])),
            record.get("workers", workers[0]),
        ].append(record)
    rows = []
    for (scenario, profile, server, count), samples in sorted(groups.items()):
        valid = [sample for sample in samples if sample["valid"]]
        complete = len(valid) == len(samples) == manifest["parameters"]["repeats"]
        row = {
            "scenario": scenario,
            "profile": profile,
            "server": server,
            "workers": count,
            "valid_samples": len(valid),
            "status": "complete" if complete else "INVALID / incomplete",
        }
        for key in (
            "rps",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "cpu_mean_percent",
            "cpu_peak_percent",
            "rss_mean_mib",
            "rss_peak_mib",
        ):
            row[key] = (
                statistics.median(sample[key] for sample in valid)
                if complete and all(key in sample for sample in valid)
                else ""
            )
        row["rps_min"] = min(sample["rps"] for sample in valid) if complete else ""
        row["rps_max"] = max(sample["rps"] for sample in valid) if complete else ""
        row["busiest_service"], row["service_utilization"] = busiest_service(
            valid if complete else []
        )
        loads = [s["load_cpu"]["utilization"] for s in valid if s.get("load_cpu")]
        row["load_utilization"] = (
            round(statistics.median(loads), 3) if complete and loads else ""
        )
        rows.append(row)
    return manifest, rows


PACKAGES = (
    "Django",
    "django-aiodrf",
    "django-fastdrf",
    "aiodrf-asgi-lifespan",
    "djangorestframework",
    "adrf",
    "django-ninja",
    "django-bolt",
    "fastapi",
    "litestar",
    "sqlalchemy",
    "pydantic",
    "msgspec",
    "uvicorn",
    "granian",
    "gunicorn",
    "gevent",
    "uvloop",
    "psycopg",
)
# At or above this CPU utilization a service may have limited throughput.
SERVICE_BUSY = 0.8


def _number(value) -> bool:
    return isinstance(value, (int, float))


def _environment_lines(manifest: dict) -> list[str]:
    parameters = manifest["parameters"]
    environment = manifest.get("server_environment") or {}
    packages = {
        package["name"]: package["version"]
        for package in environment.get("packages", [])
    }
    lines = [
        "## Parameters",
        "",
        f"- Frameworks: {', '.join(parameters.get('frameworks', []))}",
        f"- Servers: {', '.join(parameters.get('servers', ['all']))}; workers: {parameters.get('workers')}",
        f"- Scenarios: {len(parameters.get('scenarios', []))}",
        f"- Server CPUs: {parameters.get('server_cpus') or 'not pinned'}; load generator CPUs: {parameters.get('load_cpus') or 'not pinned'}",
        f"- Repeats: {parameters.get('repeats')}; read duration: {parameters.get('duration')} s; warmup: {parameters.get('warmup')} s",
        f"- Concurrency: {parameters.get('concurrency')}; load threads: {parameters.get('load_threads')}; requests per write sample: {parameters.get('post_requests')}",
        f"- Dataset: {manifest.get('dataset_size', '—')} articles; profile order seed: {parameters.get('seed')}",
        f"- Started: {manifest.get('started_at', '—')}; finished: {manifest.get('finished_at', '—')}",
    ]
    if manifest.get("oha_version"):
        lines.append(f"- Load generator: {manifest['oha_version']}")
    if environment:
        lines += [
            "",
            "## Environment",
            "",
            f"- Python: {environment.get('python', '—')}; GIL enabled: {environment.get('gil_enabled', '—')}",
            f"- Host: {environment.get('cpu_model') or '—'}, {environment.get('cpu_count', '—')} logical CPUs, "
            f"{environment.get('memory_gib') or '—'} GiB; CPU frequency governor: {environment.get('cpu_governor') or '—'}",
            f"- Platform: {environment.get('platform', '—')}",
            "- Packages: "
            + ", ".join(
                f"{name} {packages[name]}" for name in PACKAGES if name in packages
            ),
        ]
        for name, source in environment.get("sources", {}).items():
            state = {True: "modified", False: "clean", None: "unknown"}[
                source.get("git_dirty")
            ]
            lines.append(
                f"- Source {name}: `{source.get('python_sha256', '')[:12]}` "
                f"(commit {source.get('git_head') or 'none'}, working tree {state})"
            )
    if manifest.get("profile_settings"):
        lines += [
            "",
            "## Profile configuration",
            "",
            "| Profile | ORM | Serializer backend | AIODRF options |",
            "| --- | --- | --- | --- |",
        ]
        for profile, config in manifest["profile_settings"].items():
            backend = (
                "Pydantic"
                if profile == "fastapi"
                else "msgspec Struct"
                if profile == "litestar"
                else "Pydantic"
                if profile == "ninja"
                else config["serializer_backend"]
            )
            lines.append(
                f"| {profile} | {config['orm']} | {backend} | `{json.dumps(config['AIODRF'], sort_keys=True)}` |"
            )
        lines += [
            "",
            "Full parser, renderer and fastdrf settings are in `manifest.json`. SQLAlchemy uses a request-scoped AsyncSession and a worker-scoped async pool; Django uses its psycopg pool. SQL text and transaction management differ between ORMs.",
            "",
        ]
    database = manifest.get("database") or {}
    if database:
        lines.append(
            "- PostgreSQL: "
            + ", ".join(
                f"{key} {database[key]}"
                for key in (
                    "server_version",
                    "route",
                    "shared_buffers",
                    "synchronous_commit",
                    "max_connections",
                )
                if key in database
            )
            + f"; application pool {database.get('application_pool', '—')}"
        )
    if manifest.get("server_commands"):
        lines += ["", "Server commands:", ""]
        lines += [
            f"- {name}: `{' '.join(command)}`"
            for name, command in sorted(manifest["server_commands"].items())
        ]
    return lines + [""]


def _fmt(value, digits=0) -> str:
    return f"{value:,.{digits}f}" if _number(value) else "—"


def _spread(row) -> str:
    if not (_number(row["rps"]) and _number(row["rps_min"]) and row["rps"]):
        return "—"
    return f"±{(row['rps_max'] - row['rps_min']) / 2 / row['rps'] * 100:.1f}%"


def _service(row) -> str:
    if not _number(row.get("service_utilization")):
        return "—"
    mark = " ⚠" if row["service_utilization"] >= SERVICE_BUSY else ""
    return f"{row['busiest_service']} {row['service_utilization'] * 100:.0f}%{mark}"


def _load(row) -> str:
    if not _number(row.get("load_utilization")):
        return "—"
    mark = " ⚠" if row["load_utilization"] >= SERVICE_BUSY else ""
    return f"{row['load_utilization'] * 100:.0f}%{mark}"


def _scenario_lines(scenario: str, rows: list[dict], counts: list[int]) -> list[str]:
    from bench.profiles import DESCRIPTIONS

    configs = list(dict.fromkeys((r["profile"], r["server"]) for r in rows))
    by = {(r["profile"], r["server"], r["workers"]): r for r in rows}
    last = counts[-1]
    configs.sort(
        key=lambda c: (
            -(
                by.get((*c, last), {}).get("rps")
                if _number(by.get((*c, last), {}).get("rps"))
                else -1
            )
        )
    )
    header = "| Framework | Server |"
    rule = "| --- | --- |"
    for count in counts:
        header += f" {count}w req/s | {count}w spread | {count}w p99 ms |"
        rule += " ---: | ---: | ---: |"
    header += f" {last}w CPU % | {last}w busiest service | {last}w load generator |"
    rule += " ---: | --- | ---: |"
    lines = [
        f"### {scenario}",
        "",
        DESCRIPTIONS.get(scenario, "") + ".",
        "",
        f"![{scenario}](graphs/{scenario}.png)",
        "",
        header,
        rule,
    ]
    for profile, server in configs:
        cells = f"| {profile} | {server} |"
        for count in counts:
            row = by.get((profile, server, count))
            if row is None:
                cells += " — | — | — |"
            elif row["status"] != "complete":
                cells += " invalid | — | — |"
            else:
                cells += (
                    f" {_fmt(row['rps'])} | {_spread(row)} | {_fmt(row['p99_ms'], 1)} |"
                )
        row = by.get((profile, server, last), {})
        cells += (
            f" {_fmt(row.get('cpu_mean_percent'))} | {_service(row) if row else '—'} |"
            f" {_load(row) if row else '—'} |"
        )
        lines.append(cells)
    return lines + [""]


def merge(base: Path, correction: Path, output: Path, *, reason: str) -> None:
    """
    Copy run ``base`` to ``output`` with the scenarios that run ``correction``
    measured replaced by its samples; the manifest records the correction.
    """
    manifest = json.loads((base / "manifest.json").read_text())
    fixed = json.loads((correction / "manifest.json").read_text())
    for key in (
        "frameworks",
        "servers",
        "workers",
        "repeats",
        "duration",
        "concurrency",
    ):
        if manifest["parameters"].get(key) != fixed["parameters"].get(key):
            raise ValueError(f"The correction run measured other {key}.")
    scenarios = fixed["parameters"]["scenarios"]
    if not set(scenarios) <= set(manifest["parameters"]["scenarios"]):
        raise ValueError("The correction run measured scenarios the base did not.")

    def cases(run: Path) -> set[str]:
        # The case directories of the corrected scenarios, as the runner names them.
        return {
            f"{r['repeat']:02d}-{r['scenario']}-{r['profile']}-{r['server']}-w{r['workers']}"
            for r in json.loads((run / "samples.json").read_text())
            if r["scenario"] in scenarios
        }

    replaced = cases(base)
    shutil.copytree(
        base,
        output,
        ignore=lambda folder, names: [
            name for name in names if folder == str(base) and name in replaced
        ],
    )
    for name in cases(correction):
        shutil.copytree(correction / name, output / name)
    samples = [
        record
        for record in json.loads((base / "samples.json").read_text())
        if record["scenario"] not in scenarios
    ] + json.loads((correction / "samples.json").read_text())
    manifest["state"] = (
        "complete" if all(record.get("valid") for record in samples) else "failed"
    )
    manifest.setdefault("corrections", []).append(
        {
            "run": correction.name,
            "scenarios": scenarios,
            "reason": reason,
            "started_at": fixed.get("started_at"),
            "finished_at": fixed.get("finished_at"),
            "server_environment": fixed.get("server_environment"),
        }
    )
    (output / "samples.json").write_text(json.dumps(samples, indent=2))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    for stale in ("REPORT.md", "summary.csv"):
        (output / stale).unlink(missing_ok=True)
    shutil.rmtree(output / "graphs", ignore_errors=True)


def write_report(directory: Path) -> None:
    """REPORT.md, summary.csv and graphs/ of a run directory."""
    from bench.visualize import overview, scenario_chart

    manifest, rows = aggregate(directory)
    with (directory / "summary.csv").open("w", newline="") as output:
        fields = list(rows[0]) if rows else ["scenario", "profile", "workers", "status"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    parameters = manifest["parameters"]
    graphs = directory / "graphs"
    graphs.mkdir(exist_ok=True)
    counts = sorted({row["workers"] for row in rows})
    scenarios = list(dict.fromkeys(row["scenario"] for row in rows))
    configs = {(r["profile"], r["server"], r["workers"]) for r in rows}
    complete = sum(row["status"] == "complete" for row in rows)
    caption = (
        f"median of {parameters['repeats']} start(s), "
        f"{parameters.get('duration', '?')} s per read sample, "
        f"concurrency {parameters.get('concurrency', '?')}"
    )
    lines = [
        f"# Benchmark report: {directory.name}",
        "",
        f"State: **{manifest['state']}**. {len(configs)} server configurations "
        f"({len({(r['profile'], r['server']) for r in rows})} framework/server pairs "
        f"at {', '.join(map(str, counts))} worker(s)), {len(scenarios)} scenarios, "
        f"{parameters['repeats']} independent start(s) each: "
        f"{complete} of {len(rows)} groups complete.",
        "",
        "## Reading this report",
        "",
        "- Every number is the median over independent server starts; *spread* is half the min–max range relative to the median.",
        "- Configurations are `framework · server` at a worker count. Worker counts are separate results, never averaged; each worker has one dedicated CPU.",
        "- *Busiest service* is the external service (PostgreSQL, Elasticsearch, MongoDB, Valkey, HTTP fixture) with the highest CPU use during the sample, relative to the CPUs it has. "
        f"At {SERVICE_BUSY:.0%} or more (⚠) the service, not the framework, may have limited throughput.",
        "- CPU % is the server process tree's mean CPU (100 % = one CPU). *Load generator* is oha's CPU use relative to the CPUs it has; at "
        f"{SERVICE_BUSY:.0%} or more (⚠) the load generator may have limited throughput.",
        "- An *invalid* group had a failed verification, an HTTP error or a server error; it is not ranked.",
        "",
        "## Overview",
        "",
        "Throughput relative to the fastest configuration of each scenario at the same worker count (1 = fastest).",
        "",
    ]
    for count in counts:
        stem = overview(graphs, rows, count, caption)
        lines += [f"![Overview, {count} worker(s)](graphs/{stem}.png)", ""]
    lines += [
        "### Fastest configuration per scenario",
        "",
        "| Scenario | " + " | ".join(f"{c} worker(s)" for c in counts) + " |",
        "| --- |" + " --- |" * len(counts),
    ]
    for scenario in scenarios:
        cells = []
        for count in counts:
            candidates = [
                r
                for r in rows
                if r["scenario"] == scenario
                and r["workers"] == count
                and _number(r["rps"])
            ]
            top = max(candidates, key=lambda r: r["rps"], default=None)
            cells.append(
                f"{top['profile']} · {top['server']} ({_fmt(top['rps'])})"
                if top
                else "—"
            )
        lines.append(f"| [{scenario}](#{scenario}) | " + " | ".join(cells) + " |")
    busy = [
        r
        for r in rows
        if _number(r.get("service_utilization"))
        and r["service_utilization"] >= SERVICE_BUSY
    ]
    lines += ["", "## Service headroom", ""]
    peak: dict[str, dict] = {}
    for row in rows:
        if _number(row.get("service_utilization")) and (
            row["scenario"] not in peak
            or row["service_utilization"] > peak[row["scenario"]]["service_utilization"]
        ):
            peak[row["scenario"]] = row
    if peak:
        lines += [
            "The highest CPU utilization any external service reached in each scenario, over all configurations.",
            "",
            "| Scenario | Service | Utilization | Configuration |",
            "| --- | --- | ---: | --- |",
        ]
        for scenario in scenarios:
            row = peak.get(scenario)
            if row:
                lines.append(
                    f"| {scenario} | {row['busiest_service']} | {row['service_utilization'] * 100:.0f}% | "
                    f"{row['profile']} · {row['server']} · {row['workers']}w |"
                )
        lines += [
            "",
            f"{len(busy)} group(s) reached {SERVICE_BUSY:.0%}."
            if busy
            else f"No service reached {SERVICE_BUSY:.0%} in any group.",
        ]
    else:
        lines.append("Service CPU use was not recorded in this run.")
    saturated = [
        r
        for r in rows
        if _number(r.get("load_utilization")) and r["load_utilization"] >= SERVICE_BUSY
    ]
    if any(_number(r.get("load_utilization")) for r in rows):
        lines += ["", "### Load generator", ""]
        if saturated:
            lines += [
                f"oha used {SERVICE_BUSY:.0%} or more of its CPUs in these groups; their throughput may be the load generator's limit:",
                "",
            ]
            lines += [
                f"- {r['scenario']}: {r['profile']} · {r['server']} · {r['workers']}w "
                f"({r['load_utilization'] * 100:.0f}%, {_fmt(r['rps'])} req/s)"
                for r in saturated
            ]
        else:
            lines.append(
                f"oha stayed below {SERVICE_BUSY:.0%} of its CPUs in every group."
            )
    lines += ["", "## Scenarios", ""]
    for scenario in scenarios:
        selected = [r for r in rows if r["scenario"] == scenario]
        scenario_chart(graphs, scenario, selected, caption)
        lines += _scenario_lines(scenario, selected, counts)
    records = json.loads((directory / "samples.json").read_text())
    failures = [r for r in records if not r.get("valid")]
    if failures:
        lines += [
            "## Failed samples",
            "",
            "| Scenario | Configuration | Repeat | Error |",
            "| --- | --- | ---: | --- |",
        ]
        for record in failures:
            error = (
                str(record.get("error", ""))
                .replace("|", "\\|")
                .replace("\n", " ")[:200]
            )
            lines.append(
                f"| {record['scenario']} | {record['profile']} · {record.get('server', '')} · "
                f"{record.get('workers', '')}w | {record.get('repeat', '')} | {error} |"
            )
        lines.append("")
    for correction in manifest.get("corrections", []):
        lines += [
            "## Corrections",
            "",
            f"The samples of {', '.join(correction['scenarios'])} come from a "
            f"later run, `{correction['run']}` ({correction['started_at']} to "
            f"{correction['finished_at']}), with the same configurations and "
            "parameters; the samples of the first run for these scenarios were "
            "removed. " + correction["reason"],
            "",
        ]
    lines += _environment_lines(manifest)
    lines += [
        "## Files",
        "",
        "`summary.csv` holds every group's numbers; `samples.json` every sample; "
        "each case directory the load generator output, server log, resource samples "
        "and verification record; `manifest.json` the complete environment.",
    ]
    (directory / "REPORT.md").write_text("\n".join(lines) + "\n")

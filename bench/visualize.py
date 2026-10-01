"""Charts of a run: one per scenario and an overview per worker count.

Every framework has one colour in every chart and every server one hatch
pattern, so that a configuration is recognisable across the report.
"""

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from bench.profiles import PROFILES
from bench.servers import SERVERS

FRAMEWORK_COLORS = dict(
    zip(
        PROFILES,
        (
            "#1f5fa8",  # aiodrf
            "#5ba3e0",  # aiodrf-tuned
            "#8a5ca8",  # adrf
            "#2e9e6b",  # ninja
            "#c7472c",  # django
            "#1b9aaa",  # fastapi
            "#e0a526",  # litestar
            "#6b4226",  # drf
            "#e68a73",  # django-sync
            "#4a4a4a",  # bolt
        ),
        strict=True,
    )
)
SERVER_HATCHES = dict(
    zip(SERVERS, ("", "///", "\\\\\\", "xx", "..", "oo", ""), strict=True)
)


def label(row: dict) -> str:
    return f"{row['profile']} · {row['server']}"


def _number(value) -> bool:
    return isinstance(value, (int, float))


def draw(axis, rows: list[dict], metric: str, title: str) -> None:
    """Horizontal bars of ``metric``, one per row, in the rows' order."""
    names = [label(row) for row in rows]
    valid = [_number(row[metric]) for row in rows]
    values = [row[metric] if ok else 0 for row, ok in zip(rows, valid, strict=True)]
    bars = axis.barh(
        names,
        values,
        color=[FRAMEWORK_COLORS.get(row["profile"], "#888888") for row in rows],
        edgecolor="#28323c",
        linewidth=0.4,
    )
    for bar, row in zip(bars, rows, strict=True):
        bar.set_hatch(SERVER_HATCHES.get(row["server"], ""))
    for index, (row, ok) in enumerate(zip(rows, valid, strict=True)):
        if not ok:
            axis.text(0, index, " invalid", va="center", fontsize=7, color="#9f3030")
            continue
        if metric == "rps" and _number(row.get("rps_min")):
            axis.errorbar(
                row["rps"],
                index,
                xerr=[[row["rps"] - row["rps_min"]], [row["rps_max"] - row["rps"]]],
                color="#28323c",
                capsize=2,
                linewidth=0.8,
            )
        end = (
            row["rps_max"]
            if metric == "rps" and _number(row.get("rps_max"))
            else row[metric]
        )
        axis.annotate(
            f"{row[metric]:,.1f}",
            (end, index),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=7,
        )
    axis.margins(x=0.22)
    axis.invert_yaxis()
    axis.set_title(title, fontsize=10)
    axis.tick_params(axis="y", labelsize=7)
    axis.grid(axis="x", alpha=0.18)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)


def legend(figure, rows: list[dict]) -> None:
    profiles = [p for p in PROFILES if any(r["profile"] == p for r in rows)]
    servers = [s for s in SERVERS if any(r["server"] == s for r in rows)]
    handles = [Patch(facecolor=FRAMEWORK_COLORS[p], label=p) for p in profiles]
    handles += [
        Patch(facecolor="white", edgecolor="#28323c", hatch=SERVER_HATCHES[s], label=s)
        for s in servers
    ]
    # "outside" reserves the legend's space in the constrained layout.
    figure.legend(
        handles=handles,
        loc="outside lower center",
        ncol=min(8, len(handles)),
        fontsize=7,
        frameon=False,
    )


def save(figure, directory: Path, name: str) -> None:
    try:
        for extension in ("png", "svg"):
            figure.savefig(
                directory / f"{name}.{extension}", dpi=150, bbox_inches="tight"
            )
    finally:
        plt.close(figure)


def scenario_chart(directory: Path, scenario: str, rows: list[dict], caption: str):
    """Throughput per configuration, one panel per worker count, fastest first."""
    counts = sorted({row["workers"] for row in rows})
    height = max(
        3.5, 0.26 * max(sum(r["workers"] == c for r in rows) for c in counts) + 1.5
    )
    figure, axes = plt.subplots(
        1,
        len(counts),
        figsize=(7 * len(counts), height),
        squeeze=False,
        layout="constrained",
    )
    for axis, count in zip(axes[0], counts, strict=True):
        selected = sorted(
            (r for r in rows if r["workers"] == count),
            key=lambda r: -(r["rps"] if _number(r["rps"]) else -1),
        )
        draw(
            axis,
            selected,
            "rps",
            f"{count} worker{'s' if count > 1 else ''}: requests/s",
        )
    figure.suptitle(f"{scenario}\n{caption}", fontsize=11)
    legend(figure, rows)
    save(figure, directory, scenario)


def cell_label(value: float) -> str:
    """Two decimals, rounded down so that only the best reads "1"."""
    if value >= 1:
        return "1"
    return f"{math.floor(value * 100) / 100:.2f}".removeprefix("0")


def overview(directory: Path, rows: list[dict], count: int, caption: str) -> str:
    """
    A heatmap of throughput relative to the best configuration of each
    scenario, for one worker count. Returns the chart's file stem.
    """
    selected = [r for r in rows if r["workers"] == count]
    scenarios = list(dict.fromkeys(r["scenario"] for r in selected))
    configs = sorted(
        {(r["profile"], r["server"]) for r in selected},
        key=lambda c: (PROFILES.index(c[0]), SERVERS.index(c[1])),
    )
    best = {
        s: max(
            (r["rps"] for r in selected if r["scenario"] == s and _number(r["rps"])),
            default=0,
        )
        for s in scenarios
    }
    by = {(r["profile"], r["server"], r["scenario"]): r for r in selected}
    matrix = []
    for config in configs:
        line = []
        for scenario in scenarios:
            row = by.get((*config, scenario))
            value = row["rps"] if row and _number(row["rps"]) else None
            line.append(
                value / best[scenario] if value and best[scenario] else float("nan")
            )
        matrix.append(line)
    figure, axis = plt.subplots(
        figsize=(0.42 * len(scenarios) + 3.5, 0.3 * len(configs) + 2.2),
        layout="constrained",
    )
    image = axis.imshow(matrix, cmap="viridis", vmin=0, vmax=1, aspect="auto")
    for i, line in enumerate(matrix):
        for j, value in enumerate(line):
            if value == value:  # not NaN
                axis.text(
                    j,
                    i,
                    cell_label(value),
                    ha="center",
                    va="center",
                    fontsize=5.5,
                    color="white" if value < 0.55 else "black",
                )
    axis.set_xticks(
        range(len(scenarios)), scenarios, rotation=70, ha="right", fontsize=7
    )
    axis.set_yticks(range(len(configs)), [f"{p} · {s}" for p, s in configs], fontsize=7)
    figure.colorbar(
        image, ax=axis, shrink=0.6, label="requests/s ÷ best in the scenario"
    )
    axis.set_title(
        f"{count} worker{'s' if count > 1 else ''}: throughput relative to the best configuration\n{caption}",
        fontsize=10,
    )
    stem = f"overview-{count}-worker{'s' if count > 1 else ''}"
    save(figure, directory, stem)
    return stem

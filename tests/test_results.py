"""Worker isolation and independently generated graphical report artifacts."""

import json

import pytest

from bench.results import aggregate, write_report


def test_throughput_bars_show_numeric_labels():
    import matplotlib.pyplot as plt

    from bench.visualize import draw

    figure, axis = plt.subplots()
    try:
        draw(
            axis,
            [
                {
                    "profile": "django",
                    "server": "uvicorn",
                    "workers": 1,
                    "rps": 1234.5,
                    "rps_min": 1200,
                    "rps_max": 1300,
                }
            ],
            "rps",
            "Requests / second",
        )
        assert any("1,234.5" in text.get_text() for text in axis.texts)
    finally:
        plt.close(figure)


def _run(tmp_path, samples, frameworks, workers=(1,), scenarios=("json",)):
    manifest = {
        "state": "complete",
        "parameters": {
            "mode": "baremetal",
            "duration": 5,
            "concurrency": 64,
            "repeats": 1,
            "frameworks": list(frameworks),
            "servers": ["all"],
            "scenarios": list(scenarios),
            "workers": list(workers),
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "samples.json").write_text(json.dumps(samples))


def _sample(profile, server, workers, rps, scenario="json", **extra):
    return {
        "scenario": scenario,
        "profile": profile,
        "server": server,
        "workers": workers,
        "repeat": 1,
        "valid": True,
        "rps": rps,
        "p50_ms": 1,
        "p95_ms": 2,
        "p99_ms": 3,
        **extra,
    }


def test_workers_are_never_averaged_and_missing_samples_stay_visible(tmp_path):
    _run(tmp_path, [_sample("fastapi", "uvicorn", 1, 100)], ["fastapi"], workers=(1, 4))
    _, rows = aggregate(tmp_path)
    by = {(r["server"], r["workers"]): r for r in rows}
    assert by["uvicorn", 1]["rps"] == 100
    assert by["uvicorn", 4]["rps"] == ""
    assert "INVALID" in by["uvicorn", 4]["status"]
    write_report(tmp_path)
    for name in ("json", "overview-1-worker", "overview-4-workers"):
        assert (tmp_path / "graphs" / f"{name}.png").read_bytes().startswith(b"\x89PNG")
        assert "<svg" in (tmp_path / "graphs" / f"{name}.svg").read_text()
    report = (tmp_path / "REPORT.md").read_text()
    assert "| fastapi | uvicorn | 100 |" in report
    assert "invalid" in report


def test_the_report_ranks_configurations_and_flags_busy_services(tmp_path):
    busy = {"elasticsearch": {"cores": 3.6, "cpus": 4, "utilization": 0.9}}
    idle = {"elasticsearch": {"cores": 0.4, "cpus": 4, "utilization": 0.1}}
    samples = [
        _sample("litestar", "granian-asgi", 1, 300, service_cpu=busy),
        _sample("drf", "gunicorn-sync", 1, 100, service_cpu=idle),
    ]
    _run(tmp_path, samples, ["litestar", "drf"])
    write_report(tmp_path)
    report = (tmp_path / "REPORT.md").read_text()
    assert "| [json](#json) | litestar · granian-asgi (300) |" in report
    assert report.index("| litestar | granian-asgi |") < report.index(
        "| drf | gunicorn-sync |"
    )
    assert "elasticsearch 90% ⚠" in report
    assert "1 group(s) reached 80%." in report
    assert "![json](graphs/json.png)" in report


def test_failed_samples_are_listed_with_their_error(tmp_path):
    failed = {
        **_sample("bolt", "bolt-native", 1, 0),
        "valid": False,
        "error": "ContractError: boom",
    }
    del failed["rps"]
    _run(tmp_path, [failed], ["bolt"])
    write_report(tmp_path)
    report = (tmp_path / "REPORT.md").read_text()
    assert "## Failed samples" in report
    assert "ContractError: boom" in report


def test_heatmap_cells_below_the_best_never_read_as_zero_or_one():
    from bench.visualize import cell_label

    assert cell_label(1.0) == "1"
    assert cell_label(0.996) == ".99"
    assert cell_label(0.5) == ".50"
    assert cell_label(0.004) == ".00"


def test_the_scenario_chart_legend_does_not_cover_the_panels(tmp_path):
    import matplotlib.pyplot as plt

    import bench.visualize as visualize

    rows = [
        {
            "profile": profile,
            "server": server,
            "workers": workers,
            "rps": 100.0 * (i + 1),
            "rps_min": 90.0 * (i + 1),
            "rps_max": 110.0 * (i + 1),
        }
        for workers in (1, 4)
        for i, (profile, server) in enumerate(
            [
                ("aiodrf", "uvicorn"),
                ("drf", "gunicorn-sync"),
                ("drf", "gunicorn-gthread"),
                ("bolt", "bolt-native"),
            ]
        )
    ]
    figures = []
    saved = visualize.save
    visualize.save = lambda figure, directory, name: figures.append(figure)
    try:
        visualize.scenario_chart(tmp_path, "json", rows, "caption")
    finally:
        visualize.save = saved
    (figure,) = figures
    try:
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        (legend,) = figure.legends
        box = legend.get_window_extent(renderer)
        for axis in figure.axes:
            assert not box.overlaps(axis.get_tightbbox(renderer))
    finally:
        plt.close(figure)


def test_a_correction_run_replaces_its_scenarios_and_is_reported(tmp_path):
    from bench.results import merge

    base, correction, merged = (tmp_path / name for name in ("base", "fix", "merged"))
    for directory in (base, correction):
        directory.mkdir()
    failed = {**_sample("bolt", "bolt-native", 1, 0, scenario="db"), "valid": False}
    _run(
        base,
        [_sample("bolt", "bolt-native", 1, 500), failed],
        ["bolt"],
        scenarios=("json", "db"),
    )
    (base / "01-db-bolt-bolt-native-w1").mkdir()
    (base / "01-json-bolt-bolt-native-w1").mkdir()
    # A scenario whose name starts with the corrected one's is kept.
    (base / "01-db-cache-hit-bolt-bolt-native-w1").mkdir()
    _run(
        correction,
        [_sample("bolt", "bolt-native", 1, 90, scenario="db")],
        ["bolt"],
        scenarios=("db",),
    )
    (correction / "01-db-bolt-bolt-native-w1").mkdir()
    (correction / "01-db-bolt-bolt-native-w1" / "oha.json").write_text("{}")

    merge(base, correction, merged, reason="The db fixture was broken.")

    samples = json.loads((merged / "samples.json").read_text())
    assert [(s["scenario"], s["rps"]) for s in samples] == [("json", 500), ("db", 90)]
    assert (merged / "01-db-bolt-bolt-native-w1" / "oha.json").exists()
    assert (merged / "01-json-bolt-bolt-native-w1").is_dir()
    assert (merged / "01-db-cache-hit-bolt-bolt-native-w1").is_dir()
    manifest = json.loads((merged / "manifest.json").read_text())
    assert manifest["state"] == "complete"
    assert manifest["parameters"]["scenarios"] == ["json", "db"]
    (entry,) = manifest["corrections"]
    assert entry["scenarios"] == ["db"]
    assert entry["reason"] == "The db fixture was broken."
    write_report(merged)
    report = (merged / "REPORT.md").read_text()
    assert "## Corrections" in report
    assert "The db fixture was broken." in report
    assert "## Failed samples" not in report


def test_a_correction_must_measure_the_same_configurations(tmp_path):
    from bench.results import merge

    base, correction = tmp_path / "base", tmp_path / "fix"
    for directory in (base, correction):
        directory.mkdir()
    _run(base, [_sample("bolt", "bolt-native", 1, 1)], ["bolt"])
    _run(correction, [_sample("drf", "gunicorn-sync", 1, 1)], ["drf"])
    with pytest.raises(ValueError, match="frameworks"):
        merge(base, correction, tmp_path / "merged", reason="x")

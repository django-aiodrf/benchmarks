"""The published comparison selects seven profiles and relational workloads only."""

import json
import subprocess
import sys

from bench.processes import environment
from bench.profiles import COMPARISON_PROFILES, COMPARISON_SCENARIOS, required_services
from bench.servers import server_matrix


def test_comparison_servers_and_services():
    matrix = server_matrix(
        COMPARISON_PROFILES,
        ["uvicorn", "granian-asgi", "gunicorn-gthread", "granian-wsgi"],
        [1],
    )
    assert len(matrix) == len(set(matrix)) == 14
    for profile, server, workers in matrix:
        assert workers == 1
        assert server in (
            {"gunicorn-gthread", "granian-wsgi"}
            if profile.startswith("drf")
            else {"uvicorn", "granian-asgi"}
        )
    assert required_services(COMPARISON_SCENARIOS) == {"postgres"}


def test_fastdrf_profiles_compile_the_model_serializers():
    code = """
import json
import django
django.setup()
from django.conf import settings
from importlib import import_module
from fastdrf.compiler import report_details
adapter = import_module("bench.adapters." + ("aiodrf" if settings.PROFILE.startswith("aiodrf") else "drf"))
print(json.dumps({name: report_details(getattr(adapter.serializers, name)(), backend="msgspec").eligible
                  for name in ("author", "article", "user")}))
"""
    for profile in ("drf-fastdrf", "aiodrf-fastdrf"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=environment(profile),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert json.loads(result.stdout) == {
            "author": True,
            "article": True,
            "user": True,
        }

"""The stream and JWT cases: payload sizes, the token contract, load commands."""

import json
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command

from bench.auth import USER, auser, bearer, token
from bench.fixtures import article_page, fixture_article
from bench.profiles import (
    AUTH_SCENARIOS,
    PATHS,
    SCENARIOS,
    STREAM_SCENARIOS,
    required_services,
    stream_items,
)
from bench.runner import load_command


@pytest.mark.parametrize(
    ("scenario", "size"), [("stream-1k", 1024), ("stream-10k", 10240)]
)
def test_stream_bodies_have_their_size(scenario, size):
    lines = [
        json.dumps(item, separators=(",", ":")) + "\n"
        for item in stream_items(scenario)
    ]
    assert len(lines) == 8
    assert len({len(line) for line in lines}) == 1
    assert len("".join(lines).encode()) == size


def test_the_extended_cases_are_part_of_every_run():
    for scenario in (*STREAM_SCENARIOS, *AUTH_SCENARIOS):
        assert scenario in SCENARIOS
    assert required_services(STREAM_SCENARIOS) == set()
    assert required_services(AUTH_SCENARIOS) == {"postgres"}


def test_bearer_reads_the_token():
    assert bearer(f"Bearer {token()}") == token()
    assert bearer(f"bearer {token()}") == token()
    assert bearer("Token abc") is None
    assert bearer("Bearer") is None
    assert bearer("") is None


@pytest.mark.django_db
def test_the_token_loads_the_seeded_user(settings):
    settings.BENCH_DATASET_SIZE = 20
    call_command("seed_benchmark", verbosity=0)
    user = async_to_sync(auser)(token())
    assert {"id": user.id, "username": user.username} == USER
    assert async_to_sync(auser)(token() + "x") is None
    assert async_to_sync(auser)(token(999)) is None


def test_only_the_jwt_cases_send_the_token():
    args = SimpleNamespace(
        load_threads=2,
        concurrency=16,
        warmup=2,
        duration=10,
        post_requests=100,
        host="127.0.0.1",
        port=8100,
        load_cpus=None,
    )
    for scenario in AUTH_SCENARIOS:
        command = load_command("oha", args, scenario)
        assert command[command.index("-H") + 1] == f"Authorization: Bearer {token()}"
        assert command[-1] == f"http://127.0.0.1:8100{PATHS[scenario]}"
    for scenario in ("json", "stream-1k"):
        assert "-H" not in load_command("oha", args, scenario)


def test_the_page_fixture():
    assert article_page(2, 20, 1000) == [fixture_article(i) for i in range(21, 41)]
    assert article_page(2, 20, 30) == [fixture_article(i) for i in range(21, 31)]


def test_the_load_generator_is_pinned_with_one_thread_per_cpu():
    args = SimpleNamespace(
        load_threads=2,
        concurrency=64,
        warmup=2,
        duration=10,
        post_requests=100,
        host="127.0.0.1",
        port=8100,
        load_cpus=[8, 10, 12, 14],
    )
    command = load_command("oha", args, "json")
    assert command[:3] == ["taskset", "-c", "8,10,12,14"]
    assert command[command.index("--worker-threads") + 1] == "4"

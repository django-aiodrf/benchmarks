"""Contract checks for the diagnostic ASGI driver, without live services."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bench.fixtures import author_values, tag_values
from bench.profiles import CREATE_BODY, JSON_BODY

spec = importlib.util.spec_from_file_location(
    "profile_cases", Path(__file__).parents[1] / "tools" / "profile_cases.py"
)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def test_driver_sends_body_and_lifespan_state():
    clients = object()

    async def application(scope, receive, send):
        assert scope["method"] == "POST"
        assert scope["path"] == "/articles"
        assert scope["state"]["resources"] is clients
        assert json.loads((await receive())["body"]) == CREATE_BODY
        await send({"type": "http.response.start", "status": 201})
        await send({"type": "http.response.body", "body": b'{"id":'})
        await send({"type": "http.response.body", "body": b"1001}"})

    assert (
        asyncio.run(driver.request(application, clients, "article-create"))
        == b'{"id":1001}'
    )


def test_driver_rejects_error_status():
    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 500})

    with pytest.raises(ValueError, match="unexpected ASGI response"):
        asyncio.run(driver.request(application, None, "json"))


def test_profile_checks_values_not_only_status():
    driver.verify_content("json", json.dumps(JSON_BODY), 1000)
    with pytest.raises(ValueError, match="differs from fixture"):
        driver.verify_content("json", b"{}", 1000)
    created = {
        "id": 1001,
        "title": CREATE_BODY["title"],
        "body": CREATE_BODY["body"],
        "author": author_values(1),
        "tags": [tag_values(1), tag_values(2)],
    }
    driver.verify_content("article-create", json.dumps(created), 1000)
    created["id"] = 1
    with pytest.raises(ValueError, match="new primary key"):
        driver.verify_content("article-create", json.dumps(created), 1000)


def test_profile_prepares_its_selected_fixture_size(monkeypatch):
    from bench import infrastructure, processes

    seed = Mock()
    prepare_services = Mock()
    monkeypatch.setattr(processes, "seed", seed)
    monkeypatch.setattr(infrastructure, "prepare_services", prepare_services)
    driver.prepare_inputs(SimpleNamespace(scenarios=["mongo-read"], dataset_size=1000))
    seed.assert_not_called()
    prepare_services.assert_called_once_with({"mongodb"}, size=1000, reset=True)

    driver.prepare_inputs(
        SimpleNamespace(scenarios=["db", "cache-read"], dataset_size=20)
    )
    seed.assert_called_once_with(reset=True, migrate=True)
    prepare_services.assert_called_with({"postgres", "valkey"}, size=20, reset=True)

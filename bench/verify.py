"""The response contract of every workload, checked before and after each sample."""

import json

import httpx

from bench.auth import USER, token
from bench.fixtures import article_page, author_values, fixture_article, tag_values
from bench.profiles import (
    AUTH_PAGE,
    AUTH_PAGE_SIZE,
    CORE_SCENARIOS,
    CREATE_BODY,
    JSON_10K_BODY,
    JSON_BODY,
    PATHS,
    SERVICE_SCENARIOS,
    SERVICE_WRITES,
    STREAM_SCENARIOS,
    method_for,
    status_for,
    stream_items,
)
from bench.services import ServiceConfig, expected_service


class ContractError(ValueError):
    """A response does not implement the shared workload contract."""


def expect(response: httpx.Response, status: int, value=None):
    if response.status_code != status:
        raise ContractError(
            f"{response.request.url}: expected {status}, received {response.status_code}: {response.text[:500]}"
        )
    if not response.headers.get("content-type", "").startswith("application/json"):
        raise ContractError("Expected application/json")
    actual = response.json()
    if value is not None and actual != value:
        raise ContractError(
            f"{response.request.url}: JSON differs from the dataset contract"
        )
    return actual


def verify(
    base_url: str, *, dataset_size: int, write: bool = True, scenarios=CORE_SCENARIOS
) -> dict:
    """Verify all read paths; optionally persist and retrieve one new article."""
    with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
        if "json" in scenarios:
            expect(client.get("/json"), 200, JSON_BODY)
        if "json-10k" in scenarios:
            expect(client.get("/json-10k"), 200, JSON_10K_BODY)
        if "db" in scenarios:
            expect(client.get("/db"), 200, [author_values(i) for i in range(1, 11)])
        if "articles" in scenarios or "article-create" in scenarios:
            expect(
                client.get("/articles"),
                200,
                {
                    "total": dataset_size,
                    "items": [fixture_article(i) for i in range(1, 21)],
                },
            )
        if "article-detail" in scenarios:
            expect(client.get("/articles/1"), 200, fixture_article(1))
            missing = client.get("/articles/2147483647")
            if missing.status_code != 404:
                raise ContractError("Missing article must return 404")
        for name in STREAM_SCENARIOS:
            if name in scenarios:
                verify_stream(client, name)
        if "jwt-article" in scenarios or "jwt-articles" in scenarios:
            verify_authentication(client, set(scenarios), dataset_size)
        for name in scenarios:
            if name in SERVICE_SCENARIOS:
                if name in SERVICE_WRITES and not write:
                    continue
                wrong_method = "GET" if name in SERVICE_WRITES else "POST"
                if client.request(wrong_method, PATHS[name]).status_code != 405:
                    raise ContractError(f"{name} must reject {wrong_method}")
                expect(
                    client.request(method_for(name), PATHS[name]),
                    status_for(name),
                    expected_service(name, dataset_size, ServiceConfig.from_env()),
                )
        if write and "article-create" in scenarios:
            for invalid in (
                {},
                {**CREATE_BODY, "title": ""},
                {**CREATE_BODY, "author_id": 999999},
                {**CREATE_BODY, "tag_ids": [999999]},
            ):
                response = client.post("/articles", json=invalid)
                if response.status_code not in (400, 422):
                    raise ContractError(
                        f"Invalid create input must return 400 or 422; received {response.status_code}: {response.text[:500]}"
                    )
            created = expect(client.post("/articles", json=CREATE_BODY), 201)
            identifier = created.get("id")
            if type(identifier) is not int or identifier <= dataset_size:
                raise ContractError(
                    "Created article must have a new integer primary key"
                )
            expected = {
                "id": identifier,
                "title": CREATE_BODY["title"],
                "body": CREATE_BODY["body"],
                "author": author_values(1),
                "tags": [tag_values(1), tag_values(2)],
            }
            if created != expected:
                raise ContractError("Created article differs from its input")
            expect(client.get(f"/articles/{identifier}"), 200, expected)
            if expect(client.get("/articles"), 200)["total"] != dataset_size + 1:
                raise ContractError(
                    "Create must persist exactly one row; invalid input must not write"
                )
    return {
        "read_contract": "passed",
        "write_contract": "passed"
        if write
        and ("article-create" in scenarios or SERVICE_WRITES.intersection(scenarios))
        else "not-run",
    }


def verify_stream(client: httpx.Client, name: str) -> None:
    response = client.get(PATHS[name])
    if response.status_code != 200:
        raise ContractError(f"{name}: expected 200, received {response.status_code}")
    if not response.headers.get("content-type", "").startswith("application/x-ndjson"):
        raise ContractError(f"{name}: expected application/x-ndjson")
    expected = "".join(
        json.dumps(item, separators=(",", ":")) + "\n" for item in stream_items(name)
    ).encode()
    if response.content != expected:
        raise ContractError(f"{name}: the stream differs from its contract")


def verify_authentication(
    client: httpx.Client, scenarios: set[str], dataset_size: int
) -> None:
    headers = {"Authorization": f"Bearer {token()}"}
    for name in scenarios & {"jwt-article", "jwt-articles"}:
        for refused in ({}, {"Authorization": f"Bearer {token()}x"}):
            if client.get(PATHS[name], headers=refused).status_code != 401:
                raise ContractError(f"{name}: a missing or invalid token must be a 401")
    if "jwt-article" in scenarios:
        expect(
            client.get(PATHS["jwt-article"], headers=headers),
            200,
            {"user": USER, "article": fixture_article(1)},
        )
    if "jwt-articles" in scenarios:
        page = expect(client.get(PATHS["jwt-articles"], headers=headers), 200)
        # Each framework's own page shape: the items are DRF's ``results`` or
        # ``items``, the total ``count`` or Litestar's ``total``.
        items = page.get("results", page.get("items"))
        count = page.get("count", page.get("total"))
        if count != dataset_size or items != article_page(
            AUTH_PAGE, AUTH_PAGE_SIZE, dataset_size
        ):
            raise ContractError("jwt-articles: the page differs from the dataset")

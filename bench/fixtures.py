"""Deterministic fixture values, also used by HTTP contract verification."""


def author_values(identifier: int) -> dict:
    return {"id": identifier, "name": f"Author {identifier}"}


def tag_values(identifier: int) -> dict:
    return {"id": identifier, "name": f"Tag {identifier}"}


def fixture_article(identifier: int) -> dict:
    tags = sorted({(identifier - 1) % 5 + 1, identifier % 5 + 1})
    return {
        "id": identifier,
        "title": f"Article {identifier}",
        "body": "Benchmark article content. " * 8,
        "author": author_values((identifier - 1) % 10 + 1),
        "tags": [tag_values(tag) for tag in tags],
    }


def article_page(page: int, size: int, total: int) -> list[dict]:
    first = (page - 1) * size + 1
    return [fixture_article(i) for i in range(first, min(first + size, total + 1))]

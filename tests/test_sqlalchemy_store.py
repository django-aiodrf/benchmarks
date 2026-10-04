"""Query budgets, eager loading and rollback for the async ORM adapter."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bench import sqlalchemy_store as store
from bench.auth import token
from bench.domain import InvalidReferences
from bench.profiles import CREATE_BODY


def test_async_store_queries_and_transactions():
    async def exercise():
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(store.Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions.begin() as session:
                session.add(store.Author(id=1, name="Author 1"))
                session.add_all([store.Tag(id=i, name=f"Tag {i}") for i in (1, 2)])
                session.add(
                    store.User(
                        id=1,
                        username="bench",
                        password="!",
                        last_login=None,
                        is_superuser=False,
                        first_name="",
                        last_name="",
                        email="",
                        is_staff=False,
                        is_active=True,
                        date_joined=datetime.now(UTC),
                    )
                )
            async with sessions() as session:
                created = await store.create_article(session, CREATE_BODY)
                assert [tag.id for tag in created.tags] == [1, 2]
                assert created.author.name == "Author 1"
            statements = []

            @event.listens_for(engine.sync_engine, "before_cursor_execute")
            def record(connection, cursor, statement, parameters, context, many):
                statements.append(statement)

            async with sessions() as session:
                assert len(await store.authors(session)) == 1
                assert len(statements) == 1
            statements.clear()
            async with sessions() as session:
                result = await store.page(session)
                assert len(statements) == 3
                assert result["total"] == 1
                assert (
                    store.article_values(result["items"][0])["title"]
                    == CREATE_BODY["title"]
                )
                assert len(statements) == 3  # serialization issues no lazy queries
            statements.clear()
            async with sessions() as session:
                user = await store.authenticated_user(session, token())
                assert user.username == "bench"
                assert (
                    store.article_values(await store.article(session, created.id))["id"]
                    == created.id
                )
                assert len(statements) == 3  # one user, joined article, tags
            statements.clear()
            async with sessions() as session:
                assert await store.authenticated_user(session, token() + "x") is None
                assert statements == []
            for invalid in ({"author_id": 9999}, {"tag_ids": [9999]}):
                async with sessions() as session:
                    with pytest.raises(InvalidReferences):
                        await store.create_article(session, {**CREATE_BODY, **invalid})
                async with sessions() as session:
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(store.Article)
                        )
                        == 1
                    )
        finally:
            await engine.dispose()

    asyncio.run(exercise())

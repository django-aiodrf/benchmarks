"""SQLAlchemy async ORM over the same tables Django migrates and seeds."""

from contextlib import asynccontextmanager
from datetime import datetime

from django.conf import settings
from sqlalchemy import (
    URL,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    joinedload,
    mapped_column,
    relationship,
    selectinload,
)

from bench.auth import user_id
from bench.domain import InvalidReferences


class Base(DeclarativeBase):
    pass


article_tags = Table(
    "app_article_tags",
    Base.metadata,
    Column("article_id", ForeignKey("app_article.id"), primary_key=True),
    Column("tag_id", ForeignKey("app_tag.id"), primary_key=True),
)


class Author(Base):
    __tablename__ = "app_author"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(80))


class Tag(Base):
    __tablename__ = "app_tag"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(40))


class Article(Base):
    __tablename__ = "app_article"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(ForeignKey("app_author.id"))
    author: Mapped[Author] = relationship(lazy="raise")
    tags: Mapped[list[Tag]] = relationship(
        secondary=article_tags, order_by=Tag.id, lazy="raise"
    )


class User(Base):
    __tablename__ = "auth_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    password: Mapped[str] = mapped_column(String(128))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_superuser: Mapped[bool] = mapped_column(Boolean)
    username: Mapped[str] = mapped_column(String(150))
    first_name: Mapped[str] = mapped_column(String(150))
    last_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(254))
    is_staff: Mapped[bool] = mapped_column(Boolean)
    is_active: Mapped[bool] = mapped_column(Boolean)
    date_joined: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def make_engine():
    config = settings.DATABASES["default"]
    if config["ENGINE"].endswith("sqlite3"):
        return create_async_engine(
            URL.create("sqlite+aiosqlite", database=str(config["NAME"]))
        )
    return create_async_engine(
        URL.create(
            "postgresql+psycopg",
            username=config["USER"],
            password=config["PASSWORD"],
            host=config["HOST"],
            port=int(config["PORT"]),
            database=config["NAME"],
        ),
        pool_size=settings.BENCH_PG_POOL_MAX,
        max_overflow=0,
        pool_timeout=10,
        connect_args={"prepare_threshold": 5},
    )


@asynccontextmanager
async def database_lifespan(app):
    engine = make_engine()
    app.state.sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield
    finally:
        await engine.dispose()
        del app.state.sessions


def article_query():
    return (
        select(Article)
        .options(joinedload(Article.author), selectinload(Article.tags))
        .order_by(Article.id)
    )


async def authors(session: AsyncSession):
    return (await session.scalars(select(Author).order_by(Author.id).limit(10))).all()


async def article(session: AsyncSession, identifier: int):
    return await session.scalar(article_query().where(Article.id == identifier))


async def page(session: AsyncSession, number: int = 1, size: int = 20):
    count = await session.scalar(select(func.count()).select_from(Article))
    rows = (
        await session.scalars(article_query().offset((number - 1) * size).limit(size))
    ).all()
    return {"total": count, "items": rows}


async def authenticated_user(session: AsyncSession, token: str):
    identifier = user_id(token)
    if identifier is None:
        return None
    return await session.scalar(select(User).where(User.id == identifier))


async def create_article(session: AsyncSession, values: dict):
    async with session.begin():
        author = await session.get(Author, values["author_id"])
        if author is None:
            raise InvalidReferences("Unknown author")
        identifiers = set(values["tag_ids"])
        tags = (
            await session.scalars(
                select(Tag).where(Tag.id.in_(identifiers)).order_by(Tag.id)
            )
        ).all()
        if len(tags) != len(identifiers):
            raise InvalidReferences("Unknown tag")
        row = Article(title=values["title"], body=values["body"], author_id=author.id)
        session.add(row)
        await session.flush()
        if tags:
            await session.execute(
                insert(article_tags),
                [{"article_id": row.id, "tag_id": tag.id} for tag in tags],
            )
        result = await session.scalar(
            article_query()
            .where(Article.id == row.id)
            .execution_options(populate_existing=True)
        )
    return result


def article_values(row):
    return {
        "id": row.id,
        "title": row.title,
        "body": row.body,
        "author": {"id": row.author.id, "name": row.author.name},
        "tags": [{"id": tag.id, "name": tag.name} for tag in row.tags],
    }

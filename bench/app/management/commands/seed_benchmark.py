"""Create deterministic rows in the benchmark application's tables only."""

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction

from bench.app.models import Article, Author, Tag
from bench.auth import USER
from bench.fixtures import fixture_article


class Command(BaseCommand):
    help = "Seed or explicitly reset the isolated benchmark dataset."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        size = settings.BENCH_DATASET_SIZE
        if not 20 <= size <= 100_000:
            raise CommandError("BENCH_DATASET_SIZE must be between 20 and 100000")
        if options["reset"] and not settings.BENCH_ALLOW_RESET:
            raise CommandError("Reset requires BENCH_ALLOW_RESET=1")
        exists = any(model.objects.exists() for model in (Article, Author, Tag))
        if exists and not options["reset"]:
            raise CommandError("Dataset exists; reset requires explicit permission")
        if exists:
            if connection.vendor == "postgresql":
                # Reset physical table growth as well as rows between samples.
                # No CASCADE: a foreign table referencing these models must fail.
                tables = [Article.tags.through, Article, Author, Tag]
                names = ", ".join(
                    connection.ops.quote_name(model._meta.db_table) for model in tables
                )
                with connection.cursor() as cursor:
                    cursor.execute(f"TRUNCATE TABLE {names} RESTART IDENTITY")
            else:
                Article.objects.all().delete()
                Author.objects.all().delete()
                Tag.objects.all().delete()
        Author.objects.bulk_create(
            [Author(id=i, name=f"Author {i}") for i in range(1, 11)]
        )
        Tag.objects.bulk_create([Tag(id=i, name=f"Tag {i}") for i in range(1, 6)])
        rows = [fixture_article(i) for i in range(1, size + 1)]
        Article.objects.bulk_create(
            [
                Article(
                    id=row["id"],
                    title=row["title"],
                    body=row["body"],
                    author_id=row["author"]["id"],
                )
                for row in rows
            ]
        )
        through = Article.tags.through
        through.objects.bulk_create(
            [
                through(article_id=row["id"], tag_id=tag["id"])
                for row in rows
                for tag in row["tags"]
            ]
        )
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(
                no_style(), [Article, Author, Tag]
            ):
                cursor.execute(statement)
        User.objects.update_or_create(
            pk=USER["id"], defaults={"username": USER["username"]}
        )
        self.stdout.write(f"Seeded {size} articles, 10 authors, 5 tags and 1 user")

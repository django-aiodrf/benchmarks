"""The django-elasticsearch-dsl documents; the runner indexes them, not model signals."""

from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from bench.app.models import SearchRecord


@registry.register_document
class SearchDocument(Document):
    id = fields.IntegerField(attr="identifier")
    title = fields.KeywordField()
    category = fields.IntegerField()
    value = fields.LongField()

    class Index:
        name = "aiodrf-benchmarks-documents"
        settings = {"number_of_shards": 1, "number_of_replicas": 0}

    class Django:
        model = SearchRecord
        ignore_signals = True
        auto_refresh = False


@registry.register_document
class WriteDocument(SearchDocument):
    class Index:
        name = "aiodrf-benchmarks-writes"
        # Acknowledged without an fsync per request, and not refreshed while
        # measured: the write workloads count documents after an explicit
        # refresh.
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "translog.durability": "async",
            "translog.sync_interval": "5s",
            "refresh_interval": "-1",
        }

    class Django:
        model = SearchRecord
        ignore_signals = True
        auto_refresh = False

"""The documents the MongoDB workloads read, and the collection they write to."""

from django.db import models
from django_mongodb_backend.fields import ObjectIdAutoField


class Document(models.Model):
    id = ObjectIdAutoField(primary_key=True)
    identifier = models.IntegerField(unique=True)
    title = models.CharField(max_length=120)
    category = models.IntegerField()
    value = models.IntegerField()

    class Meta:
        db_table = "documents"
        indexes = [
            models.Index(fields=["category", "identifier"], name="category_identifier")
        ]

    def __str__(self):
        return self.title


class WriteDocument(models.Model):
    id = ObjectIdAutoField(primary_key=True)
    title = models.CharField(max_length=120)
    value = models.IntegerField()

    class Meta:
        db_table = "writes"

    def __str__(self):
        return self.title

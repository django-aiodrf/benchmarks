"""Authors, tags and articles, and the model behind the Elasticsearch documents."""

from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=80)

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=40)

    def __str__(self):
        return self.name


class Article(models.Model):
    title = models.CharField(max_length=120)
    body = models.TextField()
    author = models.ForeignKey(Author, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.title


class SearchRecord(models.Model):
    """Unsaved model instances supply Django Elasticsearch DSL field preparation."""

    id = models.CharField(max_length=40, primary_key=True)
    identifier = models.IntegerField()
    title = models.CharField(max_length=120)
    category = models.IntegerField()
    value = models.IntegerField()

    class Meta:
        managed = False

    def __str__(self):
        return self.title

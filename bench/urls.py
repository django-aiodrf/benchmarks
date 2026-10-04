"""Django URLs of the selected profile; other frameworks route themselves."""

from importlib import import_module

from django.conf import settings
from django.urls import path

from bench.profiles import NON_DJANGO, SERVICE_SCENARIOS, STREAM_SCENARIOS

ROUTES = (
    ("json", "json"),
    ("json-10k", "large_json"),
    ("db", "database"),
    ("articles", "articles"),
    ("articles/<int:pk>", "article"),
    ("auth/articles", "authenticated_articles"),
    ("auth/articles/<int:pk>", "authenticated_article"),
)

profile = settings.PROFILE
if profile == "bolt" or profile in NON_DJANGO:
    # Bolt, FastAPI and Litestar route requests themselves.
    urlpatterns = []
elif profile == "ninja":
    from bench.adapters.ninja import api

    urlpatterns = [path("", api.urls)]
elif profile in ("django", "django-sync"):
    from bench.adapters import django

    prefix = "async_" if profile == "django" else ""
    urlpatterns = [
        path(route, getattr(django, f"{prefix}{name}_view")) for route, name in ROUTES
    ]
    urlpatterns += [
        path(
            "workloads/" + name,
            getattr(django, prefix + "service_view"),
            {"scenario": name},
        )
        for name in SERVICE_SCENARIOS
    ]
    urlpatterns += [
        path(name, getattr(django, prefix + "stream_view"), {"scenario": name})
        for name in STREAM_SCENARIOS
    ]
else:
    # DRF, adrf and aiodrf: class-based views of the same names.
    adapter = import_module(
        "bench.adapters."
        + (
            "aiodrf"
            if profile.startswith("aiodrf")
            else profile.removesuffix("-fastdrf")
        )
    )

    def view(name):
        return getattr(
            adapter, "".join(part.title() for part in name.split("_")) + "View"
        )

    urlpatterns = [path(route, view(name).as_view()) for route, name in ROUTES]
    urlpatterns += [
        path("workloads/" + name, adapter.ServiceView.as_view(), {"scenario": name})
        for name in SERVICE_SCENARIOS
    ]
    urlpatterns += [
        path(name, adapter.StreamView.as_view(), {"scenario": name})
        for name in STREAM_SCENARIOS
    ]

"""The WSGI application, built in each worker with its synchronous service clients."""

import atexit
import os
from contextlib import ExitStack

from django.core.wsgi import get_wsgi_application

from bench.services import sync_resources


class WorkerApplication:
    def __init__(self, application):
        self.application = application
        self.stack = ExitStack()
        try:
            self.resources = self.stack.enter_context(sync_resources())
        except BaseException:
            self.stack.close()
            raise

    def __call__(self, environ, start_response):
        environ["bench.resources"] = self.resources
        return self.application(environ, start_response)

    def close(self):
        self.stack.close()


def restore_synchronous_cache_closing():
    """
    django-valkey replaces Django's ``close_caches`` receiver of
    ``request_finished`` with an async one when it is imported, for its
    asynchronous caches. A WSGI worker has none, and the async receiver would
    run ``async_to_sync`` after every request (an error under gevent).
    """
    import django_valkey.base
    from django.core import signals
    from django.core.cache import close_caches

    signals.request_finished.disconnect(django_valkey.base.close_async_caches)
    signals.request_finished.connect(close_caches)


def create_application():
    """Called inside each worker; never preload clients in a parent process."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")
    from django.conf import settings

    if settings.PROFILE not in ("drf", "django-sync"):
        raise ValueError("WSGI benchmarks require drf or django-sync")
    wsgi_application = get_wsgi_application()
    restore_synchronous_cache_closing()
    application = WorkerApplication(wsgi_application)
    atexit.register(application.close)
    return application

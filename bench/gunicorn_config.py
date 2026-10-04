"""Close benchmark-owned resources on graceful Gunicorn worker exit."""


def worker_exit(server, worker):
    application = getattr(worker, "wsgi", None)
    close = getattr(application, "close", None)
    if close is not None:
        close()

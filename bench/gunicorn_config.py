"""Close benchmark-owned resources on graceful Gunicorn worker exit."""


def worker_exit(server, worker):
    application = getattr(worker, "wsgi", None)
    if application is not None:
        application.close()

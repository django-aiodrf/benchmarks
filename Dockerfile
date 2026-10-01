# The application image of Docker mode (`aiodrf-bench run --mode docker`).
# Services and the load generator stay outside; see docs/configuration.md.
FROM python:3.14.7-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /app/
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md LICENSE NOTICE.md manage.py /app/
COPY bench /app/bench
RUN pip install --no-cache-dir --no-deps .
USER 65534:65534
CMD ["python", "-m", "uvicorn", "bench.asgi:application", "--host", "127.0.0.1", "--port", "8100", "--lifespan", "on", "--no-access-log"]

# Single stage, slim base. The app is a few hundred KB of Python with no build
# step and no compiled dependencies, so a multi-stage build would add ceremony
# without removing a single byte from the final image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first: this layer is cached across every code-only rebuild.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run unprivileged, and keep the SQLite file on its own path so it can be a
# mounted volume without dragging the source tree along.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /srv
USER appuser

ENV DB_PATH=/data/runs.db \
    LOG_LEVEL=INFO

EXPOSE 8000

# /health is per-dependency, so an unhealthy container is one that has told you
# which dependency is broken — including a missing ANTHROPIC_API_KEY.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

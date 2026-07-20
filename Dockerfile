# Reproducible Client360 runtime image (RC-2).
#   build: docker build -t client360:<version> .
#   run:   docker run -e DATABASE_URL=... -e SESSION_SECRET=... -e CLIENT360_ENVIRONMENT=production \
#                     -e MICROSOFT_TOKEN_KEY=... -p 8000:8000 client360:<version>
# Migrations are applied as a separate deploy step (scripts/deploy.sh), not at container start.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is used by the container HEALTHCHECK. psycopg2-binary bundles libpq, so no build deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install pinned runtime dependencies first so the layer caches until requirements change.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code, migrations, and operational scripts (docs/tests excluded via .dockerignore).
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# Run as a non-root user.
RUN useradd --create-home --uid 10001 client360 \
    && chown -R client360:client360 /app
USER client360

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

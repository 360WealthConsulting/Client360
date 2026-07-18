# Client360 — development application image (E1.2).
#
# Deterministic build: pinned base image + fully pinned requirements.txt.
# psycopg2-binary ships manylinux wheels, so no system build toolchain is needed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first (layer caching). Versions are pinned, so the
# resulting layer is reproducible.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code, migrations, and Alembic config.
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness probe target: /health (DB-independent, public).
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source
COPY . .

# Collect static files at build time so the image is self-contained
RUN uv run python manage.py collectstatic --noinput

EXPOSE $PORT

CMD uv run gunicorn attnd.wsgi \
    --worker-class gthread \
    --workers 2 \
    --threads 4 \
    --timeout 300 \
    --bind 0.0.0.0:$PORT

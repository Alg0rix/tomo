# Tomo coordinator — production image (source tree + uv).
# See docs/deployments.md for Docker Compose usage.
FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install deps first for better layer caching.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY app ./app
COPY cli ./cli
COPY modules ./modules
COPY defaults ./defaults

RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin tomo \
    && mkdir -p /data/home /data/work \
    && chown -R tomo:tomo /app /data

ENV PATH="/app/.venv/bin:$PATH" \
    TOMO_HOME=/data/home \
    TOMO_WORK=/data/work \
    TOMO_HOST=0.0.0.0 \
    TOMO_PORT=8787 \
    PYTHONUNBUFFERED=1

USER tomo
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/login', timeout=3)"

CMD ["python", "-m", "app.main"]

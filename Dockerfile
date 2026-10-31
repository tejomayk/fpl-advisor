FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    FPL_DATA_DIR=/data PORT=8000 \
    FPL_USAGE_PATH=/data/usage/usage.json \
    FPL_USAGE_MIRROR=/data/usage.mirror/usage.json \
    FPL_EXPECT_VOLUME=/data

WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home-dir /app app \
    && mkdir -p /data && chown app:app /data
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

VOLUME /data
EXPOSE 8000

# Starts as root only to fix the volume's ownership, then drops to the app user (uid 10001).
USER root
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/fpl-advisor-web"]

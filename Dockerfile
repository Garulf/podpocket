FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev
RUN useradd --uid 1000 --create-home podpocket \
    && mkdir -p /data /cache \
    && chown podpocket:podpocket /data /cache
ENV PATH="/app/.venv/bin:$PATH" \
    PODPOCKET_DATABASE_URL=sqlite+aiosqlite:////data/podpocket.db \
    PODPOCKET_CACHE_DIR=/cache
USER podpocket
VOLUME ["/data", "/cache"]
EXPOSE 8000
CMD ["podpocket", "serve", "--host", "0.0.0.0", "--port", "8000"]

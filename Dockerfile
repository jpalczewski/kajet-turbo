# syntax=docker/dockerfile:1

FROM oven/bun:1.4.0@sha256:5ff609364c049b54eb0ff560ec96319729a972078ef2c755d758f0c6ef89c2d6 AS frontend-deps
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun ci

FROM frontend-deps AS frontend-build
COPY frontend/ .
RUN bun run build

FROM ghcr.io/astral-sh/uv:0.12.7-trixie-slim@sha256:92d38da241c7962f8f863e288cc1c39795b79b6553245f623a82db6be95bdae0 AS app-deps

WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/jpalczewski/kajet-turbo"

# openssh-client: dulwich's SubprocessSSHVendor shells out to `ssh` for git push
# over SSH (workspace auto-push). Without it: FileNotFoundError [Errno 2] 'ssh'.
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends git openssh-client && \
    rm -rf /var/lib/apt/lists/* && \
    git config --global user.email "kajet@localhost" && \
    git config --global user.name "kajet-turbo"

COPY pyproject.toml uv.lock .python-version ./
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_CACHE_DIR=/root/.cache/uv/python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv python install && uv sync --frozen --no-dev --no-install-project

FROM app-deps AS app-base

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY alembic.ini .
COPY alembic/ alembic/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["/app/entrypoint.sh"]

FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS ingress
LABEL org.opencontainers.image.source="https://github.com/jpalczewski/kajet-turbo"
RUN apk upgrade --no-cache
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /app/dist /srv

EXPOSE 80 8000

FROM app-base AS app
COPY --from=frontend-build /app/dist ./dist

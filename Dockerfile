FROM oven/bun:1.3.11@sha256:0733e50325078969732ebe3b15ce4c4be5082f18c4ac1a0f0ca4839c2e4e42a7 AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ .
RUN bun run build

FROM ghcr.io/astral-sh/uv:0.9.30-bookworm-slim@sha256:22334efe746f1b69217d455049b484d7b8cacfb2d5f42555580b62415a98e0a3 AS app-base

WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/jpalczewski/kajet-turbo"

# openssh-client: dulwich's SubprocessSSHVendor shells out to `ssh` for git push
# over SSH (workspace auto-push). Without it: FileNotFoundError [Errno 2] 'ssh'.
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && \
    rm -rf /var/lib/apt/lists/* && \
    git config --global user.email "kajet@localhost" && \
    git config --global user.name "kajet-turbo"

COPY pyproject.toml uv.lock .python-version ./
RUN uv python install && uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-dev

COPY alembic.ini .
COPY alembic/ alembic/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

CMD ["/app/entrypoint.sh"]

FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS ingress
LABEL org.opencontainers.image.source="https://github.com/jpalczewski/kajet-turbo"
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend /app/dist /srv

EXPOSE 80 8000

FROM app-base AS app
COPY --from=frontend /app/dist ./dist

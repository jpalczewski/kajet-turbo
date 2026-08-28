FROM oven/bun:1 AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ .
RUN bun run build

FROM ghcr.io/astral-sh/uv:bookworm-slim AS app-base

WORKDIR /app

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

FROM caddy:2-alpine AS ingress
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend /app/dist /srv

EXPOSE 80 8000

FROM app-base AS app
COPY --from=frontend /app/dist ./dist

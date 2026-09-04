# syntax=docker/dockerfile:1

FROM oven/bun:1.3.11@sha256:0733e50325078969732ebe3b15ce4c4be5082f18c4ac1a0f0ca4839c2e4e42a7 AS frontend-deps
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

# The CSP script-src hash changes every build (write-csp-hash.js hashes the
# SvelteKit bootstrap script, which embeds a per-build random variable name),
# so neither hash can be a literal in the Caddyfile. Bake them in here
# instead, then remove the scratch files so they aren't served as static
# assets.
RUN sed -i \
      -e "s#__CSP_SCRIPT_HASH__#$(cat /srv/csp-script-hash.txt)#" \
      -e "s#__CSP_STYLE_HASH__#$(cat /srv/csp-style-hash.txt)#" \
      /etc/caddy/Caddyfile && \
    rm /srv/csp-script-hash.txt /srv/csp-style-hash.txt

EXPOSE 80 8000

FROM app-base AS app
COPY --from=frontend-build /app/dist ./dist
# Ingress-only build artifacts (see the "ingress" stage above); harmless if
# served here too, but they have no business in this image.
RUN rm -f ./dist/csp-script-hash.txt ./dist/csp-style-hash.txt

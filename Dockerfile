# syntax=docker/dockerfile:1

FROM oven/bun:1.4.2@sha256:9114c058aeae42162ee16dd5084b95fe9473970bb6bcb5b232ab1630f0546895 AS frontend-deps
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun ci

FROM frontend-deps AS frontend-build
COPY frontend/ .
RUN bun run build

FROM ghcr.io/astral-sh/uv:0.12.10-trixie-slim@sha256:260222c52f44bbf971682a1f84b333a6110ad03b41602cea2a3350e126e004ec AS app-deps

WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/jpalczewski/kajet-turbo"

# BuildKit caches this layer by instruction text alone, so `apt-get upgrade` never
# re-runs once cached — freezing OS package versions at whatever they were on the
# first build, however old that cache gets. OS_PKG_CACHE_BUST (the build date, set
# by build-image/action.yml) is baked into the command so the layer — and every apt
# invocation in it — is at most a day stale, not indefinitely.
ARG OS_PKG_CACHE_BUST=0
# openssh-client: dulwich's SubprocessSSHVendor shells out to `ssh` for git push
# over SSH (workspace auto-push). Without it: FileNotFoundError [Errno 2] 'ssh'.
RUN echo "cache-bust: ${OS_PKG_CACHE_BUST}" && \
    apt-get update && apt-get upgrade -y && \
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
# See OS_PKG_CACHE_BUST above (app-deps stage) — same cached-forever problem applies here.
ARG OS_PKG_CACHE_BUST=0
RUN echo "cache-bust: ${OS_PKG_CACHE_BUST}" && apk upgrade --no-cache
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /app/dist /srv

# The CSP script-src hash changes every build (write-csp-hash.js hashes the
# SvelteKit bootstrap script, which embeds a per-build random variable name),
# so neither hash can be a literal in the Caddyfile. Bake them in here
# instead. write-csp-hash.js writes these next to dist/, not inside it, so
# they never end up in /srv and there's nothing to clean up afterwards.
COPY --from=frontend-build /app/csp-script-hash.txt /app/csp-style-hash.txt /tmp/
RUN sed -i \
      -e "s#__CSP_SCRIPT_HASH__#$(cat /tmp/csp-script-hash.txt)#" \
      -e "s#__CSP_STYLE_HASH__#$(cat /tmp/csp-style-hash.txt)#" \
      /etc/caddy/Caddyfile && \
    rm /tmp/csp-script-hash.txt /tmp/csp-style-hash.txt

EXPOSE 80 8000

FROM app-base AS app
COPY --from=frontend-build /app/dist ./dist

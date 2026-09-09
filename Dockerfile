# syntax=docker/dockerfile:1

FROM oven/bun:1.4.0@sha256:5ff609364c049b54eb0ff560ec96319729a972078ef2c755d758f0c6ef89c2d6 AS frontend-deps
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun ci

FROM frontend-deps AS frontend-build
COPY frontend/ .
RUN bun run build

# Build stage: owns uv, the interpreter download and the venv. Nothing from it reaches
# the runtime image except /python and /app/.venv, so the uv binary, its caches and the
# build toolchain never ship.
FROM ghcr.io/astral-sh/uv:0.12.7-trixie-slim@sha256:92d38da241c7962f8f863e288cc1c39795b79b6553245f623a82db6be95bdae0 AS app-build

WORKDIR /app

ENV UV_LINK_MODE=copy
ENV UV_PYTHON_CACHE_DIR=/root/.cache/uv/python
# An explicit, stable install dir: the venv records its interpreter by absolute path, so
# the interpreter must sit at the same path in both stages or /app/.venv/bin/python
# dangles after the copy. only-managed stops uv from quietly satisfying the requirement
# with a system interpreter that the runtime stage will not have.
ENV UV_PYTHON_INSTALL_DIR=/python
ENV UV_PYTHON_PREFERENCE=only-managed

COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv python install && uv sync --frozen --no-dev --no-install-project

COPY src/ src/
# --no-editable installs the project into the venv rather than linking back to /app/src,
# which the runtime stage deliberately does not copy.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# python-build-standalone ships pip inside the interpreter's site-packages and a second
# copy as a wheel under ensurepip. Neither is used: dependencies come from uv.lock via
# `uv sync`, and the runtime imports neither pip nor setuptools (cffi's setuptools shims
# are build-time only — the compiled _cffi_backend is already installed). uv additionally
# marks managed installs EXTERNALLY-MANAGED per PEP 668, so this pip could not install
# anything even if something invoked it. It is not inert, though: pip vendors its own
# dependency tree, which image scanners report against an image that never runs pip.
# Assert the removal instead of trusting a glob to have matched — a layout change should
# break the build loudly, not silently skip this.
RUN find /python -maxdepth 5 -type d \
        \( -name pip -o -name 'pip-*.dist-info' -o -name setuptools \
           -o -name 'setuptools-*.dist-info' -o -name ensurepip \) \
        -prune -exec rm -rf {} + && \
    rm -f /python/*/bin/pip /python/*/bin/pip[0-9]* && \
    ! /app/.venv/bin/python -c 'import pip' 2>/dev/null && \
    ! /app/.venv/bin/python -c 'import ensurepip' 2>/dev/null && \
    /app/.venv/bin/python -c 'import sqlite3, ssl, ctypes; print("interpreter ok:", sqlite3.sqlite_version)'

# Runtime stage: the same Debian release the uv image is built on, carrying only what the
# application actually executes.
FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS app-base

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
# ca-certificates: the uv build image carries them, a bare debian slim does not, and
# every outbound HTTPS call (embedding provider, git over https) needs them.
RUN echo "cache-bust: ${OS_PKG_CACHE_BUST}" && \
    apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends git openssh-client ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    git config --global user.email "kajet@localhost" && \
    git config --global user.name "kajet-turbo"

COPY --from=app-build /python /python
COPY --from=app-build /app/.venv /app/.venv

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

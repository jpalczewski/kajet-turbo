FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# Layer cache: zależności przed kodem
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-dev

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

CMD ["uv", "run", "kajet-turbo"]

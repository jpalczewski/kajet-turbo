FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# Copy project files (must include src/ for build backend)
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

CMD ["uv", "run", "kajet-turbo"]

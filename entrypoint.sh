#!/bin/sh
set -e
# Single migration owner: the mcp role shares the DB with the api role, so only
# non-mcp roles run migrations to avoid a concurrent alembic race on SQLite.
if [ "$KAJET_ROLE" != "mcp" ]; then
  uv run --no-dev alembic upgrade head
fi
exec uv run --no-dev kajet-turbo

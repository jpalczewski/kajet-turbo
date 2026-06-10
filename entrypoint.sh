#!/bin/sh
set -e
uv run --no-dev alembic upgrade head
exec uv run --no-dev kajet-turbo

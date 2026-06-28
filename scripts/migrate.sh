#!/usr/bin/env bash
# Generate or apply Alembic migrations against a local SQLite DB.
#
# Usage:
#   ./scripts/migrate.sh                        # apply pending migrations
#   ./scripts/migrate.sh revision -m "message"  # autogenerate new migration
#   ./scripts/migrate.sh current                # show current revision
#   ./scripts/migrate.sh history                # show revision history
#
# Any extra args are forwarded to `alembic`.

set -euo pipefail

DB_FILE="${MIGRATE_DB:-/tmp/kajet-migrate.db}"
INI_FILE="${TMPDIR:-/tmp}/alembic-local-$$.ini"
PROJ="$(cd "$(dirname "$0")/.." && pwd)"

# Build a throwaway ini pointing at the local DB and absolute script path.
sed \
  "s|sqlalchemy.url = .*|sqlalchemy.url = sqlite:////${DB_FILE}|; \
   s|script_location = .*|script_location = ${PROJ}/alembic|" \
  "${PROJ}/alembic.ini" > "${INI_FILE}"

trap 'rm -f "${INI_FILE}"' EXIT

export DISABLE_SQLALCHEMY_CEXT_RUNTIME=1
exec uv run alembic -c "${INI_FILE}" "${@:-upgrade head}"

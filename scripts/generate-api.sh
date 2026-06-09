#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "→ Exporting OpenAPI schema..."
cd "$PROJECT_ROOT"
uv run scripts/export_openapi.py

echo "→ Generating TypeScript client with Orval..."
cd "$PROJECT_ROOT/frontend"
bunx orval --config orval.config.ts

echo "✓ Done — client generated in frontend/src/lib/api/"

"""Shared helpers for MCP tool tests."""

import re

# Matches anything that looks like a (short or full) git sha — used to assert
# stale-sha errors never leak the current sha.
SHA_LIKE = re.compile(r"\b[0-9a-f]{7,40}\b")

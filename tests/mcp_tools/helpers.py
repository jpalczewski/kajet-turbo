"""Shared helpers for MCP tool tests."""

import re

# Matches anything that looks like a (short or full) git sha — used to assert
# stale-sha errors never leak the current sha. Floor is 8, not 7: note_ids are
# 7-char nanoids and an all-hex one would false-positive against this pattern.
SHA_LIKE = re.compile(r"\b[0-9a-f]{8,40}\b")

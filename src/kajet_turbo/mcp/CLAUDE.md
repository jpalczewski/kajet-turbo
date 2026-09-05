# MCP Tool Surface

Everything under `src/kajet_turbo/mcp/` is read by a language model before it is read by a
human. These rules follow from that.

## Write LLM-facing strings in English

Tool docstrings, `Field(description=...)`, `ToolError` text, and the `ValueError` messages
raised in `services/notes/` and `markdown/` all reach the calling model verbatim —
`logged_tool` (`log.py`) maps `SERVICE_ERRORS` (`tooling.py`) straight to `ToolError` at the
point the raw exception is still visible, before fastmcp's own `call_tool()` can wrap it in
a generic message. Write them in English. Older Polish strings are legacy: convert the ones inside a tool you are
already changing, and leave the rest alone rather than opening a translation PR.

An error message is a prompt. Name the parameter at fault and what should have been passed
instead — `"Mode 'replace_text' does not take content; it takes old_str and new_str."` —
not just that something was wrong.

## Name parameters after what clients actually send

Parameter names come from observed client behaviour, not from internal consistency. Models
carry strong priors from tools they already know, and those priors beat our published
schema: `edit_note` advertised `old_text` and still collected 54 `old_str` rejections in 30
days of production logs (issue #38). That is why the text modes now take `old_str`/`new_str`,
matching the Edit tool.

Check the evidence before renaming or adding a parameter:

```bash
./ops/fetch-logs.sh -r mcp
uv run python scripts/analyze-logs.py --grep "Invalid arguments" --re
```

## One parameter, one meaning

If a parameter's meaning depends on another parameter's value, split it. `content` used to
mean both "the new body" and "the replacement for the anchor", depending on `mode` — that
ambiguity is what produced the rejections above. Passing a parameter the chosen mode does
not own is a hard error: ignoring it silently lets a caller believe an edit landed the way
they meant.

## Where the seams are

- `build_mcp` (`__init__.py`) is the single registration site. Sub-servers are mounted
  without a prefix, so tool names stay bare.
- `Middleware.on_call_tool` runs *before* argument validation and can rewrite
  `context.message.arguments`. It is the only place raw wire arguments are visible.
- `logged_tool` sits *under* `@srv.tool`, so it only ever sees validated Python values. It
  is for timing, logging, and the `SERVICE_ERRORS` → `ToolError` boundary mapping — not
  argument preprocessing. It does not log `ToolError` — a `Depends` dependency (e.g.
  `ACTIVE_WORKSPACE`) can raise one before `logged_tool`'s wrapper ever runs, so
  `ServiceErrorMiddleware` (`tooling.py`) is the single place that logs a `ToolError`,
  whichever layer raised it.

Never log note titles or bodies — logs are shipped off-box and notes are personal.

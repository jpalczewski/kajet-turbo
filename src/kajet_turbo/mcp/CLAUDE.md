# MCP Tool Surface

Everything under `src/kajet_turbo/mcp/` is read by a language model before it is read by a
human. These rules follow from that.

## Write LLM-facing strings in English

Tool docstrings, `Field(description=...)`, `ToolError` text, and the `ValueError` messages
raised in `services/notes/` and `markdown/` all reach the calling model verbatim —
`ToolDispatchMiddleware` (`tooling.py`) recovers the original exception from the generic
`ToolError` fastmcp wrapped it in and re-raises it with its own message intact, so a
`SERVICE_ERRORS` member arrives unprefixed. Write them in English. Older Polish strings are legacy: convert the ones inside a tool you are
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
uv run python scripts/analyze-logs.py --grep rejected_params --fields ts,tool,rejected_params
```

A rejected call logs one record named after the tool, carrying `error_type=ValidationError`
and `rejected_params` — the parameter paths pydantic refused, never the values. Count by
`tool` + `rejected_params` to see which name a model keeps reaching for.

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
- `ToolDispatchMiddleware` (`tooling.py`) is the **single logging seam for tool calls**:
  exactly one structured record per `tools/call`, carrying `tool`, `duration_ms` and the
  correlation ids read off the live dispatch context. Do not add a logging wrapper under
  `@srv.tool` — one hook already covers `Depends` resolution, argument validation and the
  tool body alike, which no per-tool wrapper can (a `Depends` default like `NOTE_TARGET`
  resolves before any such wrapper runs — issue #71).
- It also owns the `SERVICE_ERRORS` → `ToolError` mapping, recovered through `__cause__`:
  fastmcp's core has already wrapped the original exception by the time the hook runs.
- fastmcp's own `Error calling tool` / `Invalid arguments for tool` lines are filtered out
  at the intercept handler (`log.py`) because the middleware owns that record. The second
  of those formats pydantic's rejected `input` value — a note body, for `save_note` — so
  the filter is a privacy boundary, not just deduplication.

Never log note titles or bodies — logs are shipped off-box and notes are personal.

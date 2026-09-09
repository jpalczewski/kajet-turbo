"""Server-rendered title/OpenGraph/Twitter-card meta tags for `/shared/{token}`, so a
link-preview crawler (Slack, Signal, iMessage, ...) that never runs JS still gets a real
title instead of the bare SPA shell. Registered on `api_router` ahead of the SPA static
mount (`server.py`'s `_mount_spa`), so it fully replaces the SPA fallback for this one
path -- browsers still get the same shell (now with the tags already in place) and
hydrate into the existing `frontend/src/routes/shared/[token]` client route exactly as
before.

Deliberately excluded from the OpenAPI schema (`include_in_schema=False`): it returns
HTML, not JSON, and that schema feeds the generated TS client (`scripts/generate-api.sh`)
-- a JSON-shaped client function for an HTML route would be nonsense. It also isn't under
`/api/`, so `tests/api/test_endpoint_contract.py`'s typed-response walk never sees it.

An unknown token and a revoked one both render with identical, neutral tags at HTTP 200 --
never a 404. A status-code (or byte-level) difference here would be a cheaper validity
oracle than `/api/public/notes/{token}` itself, which at least requires bots to run JS to
reach. See `resolve_shared_note` in `public_notes.py` for the shared resolution path and
the visit-counting note that applies here too.

`api_route(..., methods=["GET", "HEAD"])`, not a plain `@router.get`: unlike a bare
Starlette `Route`, `APIRoute` does not add HEAD to a GET route for free, and a preview
fetcher that HEADs a URL before GETing it must not 405 here.
"""

import html
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from kajet_turbo.api.public_notes import resolve_shared_note
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    get_note_read_service,
    get_note_share_link_repo,
    get_workspace_service,
)
from kajet_turbo.errors import NoteError
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.services.notes import NoteReadService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}
_DIST_INDEX = Path(__file__).parent.parent.parent.parent / "dist" / "index.html"
_HEAD_MARKER = "</head>"

_NEUTRAL_TITLE = "Link nieaktywny — kajet"
_GENERIC_DESCRIPTION = "Notatka udostępniona w kajet."
_EXCERPT_LENGTH = 200
_STATIC_TITLE_RE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)


@lru_cache(maxsize=1)
def _shell_parts() -> tuple[str, str] | None:
    """The built SPA shell, split once on `</head>` -- `None` if no build is present
    (e.g. an API-role deployment run without a frontend build), which the route below
    treats as a plain 404, not a token-related signal.

    The shell's own static `<title>` (from `frontend/src/app.html`) is stripped from
    `head` first -- otherwise the per-token `<title>` this route injects would be the
    *second* one in the document, and not every crawler picks the last `<title>` it sees."""
    if not _DIST_INDEX.exists():
        return None
    shell = _DIST_INDEX.read_text(encoding="utf-8")
    head, marker, tail = shell.partition(_HEAD_MARKER)
    if not marker:
        raise RuntimeError(f"{_DIST_INDEX} has no {_HEAD_MARKER!r} to inject meta tags before")
    head = _STATIC_TITLE_RE.sub("", head, count=1)
    return head, tail


_LEADING_HEADING_RE = re.compile(r"^\s*#{1,6}[ \t]+.*?(?:\n|$)")


def _excerpt(content: str) -> str:
    # Most notes open with a "# Title" line that just repeats what og:title already
    # carries -- drop it so the excerpt starts on actual body text instead.
    body = _LEADING_HEADING_RE.sub("", content, count=1)
    collapsed = " ".join(body.split())
    if len(collapsed) <= _EXCERPT_LENGTH:
        return collapsed
    return collapsed[:_EXCERPT_LENGTH].rsplit(" ", 1)[0] + "…"


def _meta_html(*, title: str, description: str, url: str) -> str:
    esc_title = html.escape(title)
    esc_description = html.escape(description)
    esc_url = html.escape(url)
    return (
        f"<title>{esc_title}</title>"
        f'<meta property="og:title" content="{esc_title}">'
        f'<meta property="og:type" content="website">'
        f'<meta property="og:url" content="{esc_url}">'
        f'<meta property="og:description" content="{esc_description}">'
        f'<meta name="twitter:card" content="summary">'
    )


@router.api_route("/shared/{token}", methods=["GET", "HEAD"], include_in_schema=False)
async def shared_note_preview(
    token: str,
    request: Request,
    share_link_repo: NoteShareLinkRepository = Depends(get_note_share_link_repo),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    note_read_service: NoteReadService = Depends(get_note_read_service),
) -> HTMLResponse:
    shell = _shell_parts()
    if shell is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND, headers=_NO_STORE)
    head, tail = shell

    resolved = await run_sync(
        resolve_shared_note, token, share_link_repo, workspace_service, note_read_service
    )
    if resolved is None:
        title, description = _NEUTRAL_TITLE, _GENERIC_DESCRIPTION
    else:
        link, note = resolved
        title = f"{note.title} — kajet"
        description = _excerpt(note.content) if link.preview_description else _GENERIC_DESCRIPTION

    meta = _meta_html(title=title, description=description, url=str(request.url))
    return HTMLResponse(content=head + meta + tail, headers=_NO_STORE)

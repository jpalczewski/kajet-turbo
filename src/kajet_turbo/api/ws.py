import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.exceptions import WebSocketException
from nanoid import generate
from starlette import status

from kajet_turbo import identity
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_event_repo, get_session_repo
from kajet_turbo.log import logger
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.sessions import SessionRepository

router = APIRouter()

_WS_KINDS = ["note_updated", "workspace_changed"]

# Outbox poll cadence and session re-validation cadence are independently tunable
# constants — but the revalidation deadline is only checked once per poll tick, so
# detection latency is actually bounded by max(_REVALIDATE_INTERVAL_S,
# _POLL_INTERVAL_S): raising _POLL_INTERVAL_S above _REVALIDATE_INTERVAL_S would
# silently widen it. Tests monkeypatch both — see tests/api/test_ws.py.
_POLL_INTERVAL_S = 2.0
_REVALIDATE_INTERVAL_S = 30.0


async def _get_ws_user(
    websocket: WebSocket,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> dict:
    user = await run_sync(
        identity.resolve_session_user_from_cookies, session_repo, websocket.cookies
    )
    if not user:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return user


@router.websocket("/api/ws")
async def ws_endpoint(
    websocket: WebSocket,
    user: dict = Depends(_get_ws_user),
    event_repo: EventRepository = Depends(get_event_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> None:
    # Bound for the whole connection: every line it emits — outbox reads and
    # ws_read_error included — carries the same conn_id without threading it through.
    # Same mechanism LoggingMiddleware uses to tag an HTTP request.
    conn_id = generate(size=8)
    with logger.contextualize(conn_id=conn_id):
        started = time.monotonic()
        await websocket.accept()
        logger.info("ws_connected", user_id=user["id"])

        # Cursor into the outbox, private to this connection. Starting at 0 replays
        # whatever the sweep still holds (1h), so a client that dropped — per #39 that is
        # roughly twelve times an hour — gets what it missed without any client-side
        # bookkeeping. Reads no longer consume rows, so a second tab sees them too.
        watermark = 0.0
        # Ids already sent at exactly `watermark`. The read bound has to be inclusive
        # (no monotonic cursor on this table), so this is what stops the overlapping tick
        # from being delivered twice.
        sent_at_watermark: set[str] = set()

        next_revalidate = time.monotonic() + _REVALIDATE_INTERVAL_S
        try:
            while True:
                # Client->server messages aren't part of this protocol — receive_text()
                # here is purely a disconnect signal, timeboxed so it also paces the
                # outbox poll below. A real disconnect raises WebSocketDisconnect, caught
                # by the except below to end the loop (and, via `finally`, always log
                # ws_disconnected — a plain try/finally guarantees that regardless of
                # what raises, unlike two separately-awaited background tasks would).
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(websocket.receive_text(), timeout=_POLL_INTERVAL_S)

                # Re-validate the session that authenticated the handshake, not just
                # whatever's in websocket.cookies now (those are the upgrade request's
                # cookies, fixed for the connection's life — re-reading them here just
                # avoids threading a `token` local through the closure). #79: without
                # this, logging out or expiry never stopped an already-open socket.
                if time.monotonic() >= next_revalidate:
                    next_revalidate = time.monotonic() + _REVALIDATE_INTERVAL_S
                    try:
                        current = await run_sync(
                            identity.resolve_session_user_from_cookies,
                            session_repo,
                            websocket.cookies,
                        )
                    except Exception as e:
                        # Transient DB hiccup: fail open, same as ws_read_error below —
                        # a revoked session gets caught on the next revalidation tick.
                        logger.opt(exception=e).warning("ws_revalidate_error", user_id=user["id"])
                    else:
                        if current is None:
                            logger.info("ws_session_revoked", user_id=user["id"])
                            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                            return

                try:
                    events = await run_sync(
                        event_repo.read_since,
                        user["id"],
                        _WS_KINDS,
                        watermark,
                        sent_at_watermark,
                    )
                except Exception as e:
                    logger.opt(exception=e).warning("ws_read_error", user_id=user["id"])
                    continue
                for event in events:
                    try:
                        payload = json.loads(event.payload)
                    except json.JSONDecodeError as e:
                        # A malformed row would otherwise wedge this cursor forever: the
                        # watermark only advances below, so every reconnect would replay
                        # and re-crash on the same event. Log and skip past it instead.
                        logger.opt(exception=e).warning(
                            "ws_bad_event_payload", user_id=user["id"], event_id=event.id
                        )
                    else:
                        # Advance only after the send lands: if the socket dies mid-loop
                        # the cursor stays put and the next connection re-delivers,
                        # instead of the event being lost the way a delete-on-read left it.
                        await websocket.send_json(payload)
                    if event.created_at > watermark:
                        watermark = event.created_at
                        sent_at_watermark = {event.id}
                    else:
                        sent_at_watermark.add(event.id)
        except WebSocketDisconnect:
            pass
        finally:
            logger.info(
                "ws_disconnected",
                user_id=user["id"],
                duration_s=round(time.monotonic() - started, 1),
            )

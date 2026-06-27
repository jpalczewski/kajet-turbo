import json

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from mcp.types import ClientCapabilities, ElicitationCapability


def client_supports_elicitation(ctx: Context) -> bool:
    try:
        return ctx.session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability())
        )
    except Exception:
        return False


async def confirm_and_apply(ctx: Context, result: dict, reapply) -> str:
    """Resolve a possibly-destructive service result.

    If ``result`` asks for confirmation, ask the human via elicitation when the
    client supports it and re-run ``reapply`` (the op with confirm=True) on
    accept; otherwise return the payload so the model can re-call with
    confirm=true.
    """
    if not result.get("requires_confirmation"):
        return json.dumps(result, ensure_ascii=False)
    if client_supports_elicitation(ctx):
        try:
            elicited = await ctx.elicit(result["warning"], response_type=["potwierdzam", "anuluj"])
        except Exception:
            return json.dumps(result, ensure_ascii=False)
        if isinstance(elicited, AcceptedElicitation) and elicited.data == "potwierdzam":
            return json.dumps(await reapply(), ensure_ascii=False)
        return json.dumps(
            {
                "note_id": result["note_id"],
                "cancelled": True,
                "message": "Anulowano — nic nie zmieniono.",
            },
            ensure_ascii=False,
        )
    return json.dumps(result, ensure_ascii=False)


def workspace_error(e: Exception) -> ToolError:
    return ToolError(str(e))

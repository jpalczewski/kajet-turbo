from pydantic import BaseModel

from kajet_turbo.api.schemas.base import RequestModel


class ShareLinkItem(BaseModel):
    token: str
    created_at: str
    preview_description: bool


class ShareLinksResponse(BaseModel):
    links: list[ShareLinkItem]


class CreateShareLinkRequest(RequestModel):
    preview_description: bool = False


class UpdateShareLinkPreviewRequest(RequestModel):
    preview_description: bool

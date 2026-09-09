from pydantic import BaseModel

from kajet_turbo.api.schemas.base import RequestModel


class ShareLinkItem(BaseModel):
    token: str
    created_at: str
    visit_count: int
    last_visited_at: str | None
    page_view_count: int
    last_page_viewed_at: str | None
    preview_description: bool


class ShareLinksResponse(BaseModel):
    links: list[ShareLinkItem]


class CreateShareLinkRequest(RequestModel):
    preview_description: bool = False


class UpdateShareLinkPreviewRequest(RequestModel):
    preview_description: bool

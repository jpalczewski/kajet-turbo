from pydantic import BaseModel


class ShareLinkItem(BaseModel):
    token: str
    created_at: str
    visit_count: int
    last_visited_at: str | None


class ShareLinksResponse(BaseModel):
    links: list[ShareLinkItem]

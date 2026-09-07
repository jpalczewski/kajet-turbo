from pydantic import BaseModel


class ShareLinkItem(BaseModel):
    token: str
    created_at: str


class ShareLinksResponse(BaseModel):
    links: list[ShareLinkItem]

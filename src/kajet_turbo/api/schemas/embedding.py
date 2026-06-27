from pydantic import BaseModel


class EmbeddingProfileItem(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    dim: int
    is_active: bool
    has_key: bool


class EmbeddingProfilesResponse(BaseModel):
    profiles: list[EmbeddingProfileItem]

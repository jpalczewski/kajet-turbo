from pydantic import BaseModel, ConfigDict


class CreateEmbeddingProfileRequest(BaseModel):
    # REST policy: unknown fields are dropped rather than rejected -- see notes/crud.py.
    model_config = ConfigDict(extra="ignore")

    name: str
    base_url: str
    model: str
    api_key: str | None = None


class UpdateEmbeddingProfileRequest(CreateEmbeddingProfileRequest):
    """Same fields as create -- kept as a distinct type (rather than reused directly) so
    the generated OpenAPI schema/TS client names the two operations' bodies separately.
    api_key's semantics differ slightly here: omitted or null keeps the existing sealed
    key, a non-empty value reseals it -- update() and update_profile() already implement
    that, this subclass changes no field or default."""


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

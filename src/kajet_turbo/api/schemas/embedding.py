from pydantic import BaseModel, ConfigDict


class CreateEmbeddingProfileRequest(BaseModel):
    # REST policy: unknown fields are dropped rather than rejected -- see notes/crud.py.
    model_config = ConfigDict(extra="ignore")

    name: str
    base_url: str
    model: str
    api_key: str | None = None


class UpdateEmbeddingProfileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    base_url: str
    model: str
    # Omitted or null -- keep the existing sealed key; non-empty -- reseal it.
    api_key: str | None = None


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

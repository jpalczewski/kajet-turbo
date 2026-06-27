from pydantic import BaseModel


class SshKeyItem(BaseModel):
    id: str
    name: str
    algorithm: str
    fingerprint: str
    public_key: str
    created_at: str


class SshKeysResponse(BaseModel):
    keys: list[SshKeyItem]

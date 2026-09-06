from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

# Mirrors kajet_turbo.crypto.ssh_keys.ALGORITHMS -- a type checker requires Literal's
# arguments to be literal values, not that module's constants, so a values-match test
# (tests/api/test_ssh_keys_endpoints.py) keeps the two in sync instead.
SSH_KEY_ALGORITHMS = ("ed25519", "ecdsa-p256", "rsa-4096")


def _require_name(v: str) -> str:
    """Rejects a *present but blank* name -- a missing key never reaches this validator
    (required, no default) and is mapped to SSH_KEY_NAME_REQUIRED by api/errors.py's
    required-field table instead. Mirrors CreateNoteRequest's `_require_title`."""
    stripped = v.strip()
    if not stripped:
        raise PydanticCustomError("ssh_key_name_required", "Name is required")
    return stripped


class CreateSshKeyRequest(BaseModel):
    # REST policy: unknown fields are dropped rather than rejected -- see notes/crud.py.
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    algorithm: Literal["ed25519", "ecdsa-p256", "rsa-4096"]

    _validate_name = field_validator("name")(_require_name)


class SshKeyItem(BaseModel):
    id: str
    name: str
    algorithm: str
    fingerprint: str
    public_key: str
    created_at: str


class SshKeysResponse(BaseModel):
    keys: list[SshKeyItem]

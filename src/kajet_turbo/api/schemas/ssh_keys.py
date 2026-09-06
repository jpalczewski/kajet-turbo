from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError

# Mirrors kajet_turbo.crypto.ssh_keys.ALGORITHMS -- a type checker requires Literal's
# arguments to be literal values, not that module's constants, so a values-match test
# (tests/api/test_ssh_keys_endpoints.py) keeps the two in sync instead.
SSH_KEY_ALGORITHMS = ("ed25519", "ecdsa-p256", "rsa-4096")


def _require_name(v: str) -> str:
    """Rejects a blank-or-whitespace-only name, present or not (no `min_length` on the
    field -- an empty string and a missing key would otherwise 422 with two different
    codes for the same "no name given" problem: `min_length` fires as generic
    INVALID_INPUT via api/errors.py's `_REQUIRED_FIELD_CODES`, while this validator's
    custom error type maps to the specific SSH_KEY_NAME_REQUIRED). A genuinely *missing*
    key still never reaches this validator (pydantic doesn't run one against an absent
    required field) and falls back to INVALID_INPUT -- "name" is too generic a field name
    to key by itself in that global table (see api/errors.py)."""
    stripped = v.strip()
    if not stripped:
        raise PydanticCustomError("ssh_key_name_required", "Name is required")
    return stripped


class CreateSshKeyRequest(BaseModel):
    # REST policy: unknown fields are dropped rather than rejected -- see notes/crud.py.
    model_config = ConfigDict(extra="ignore")

    name: str
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

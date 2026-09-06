from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError

from kajet_turbo.api.schemas.base import RequestModel
from kajet_turbo.errors import ErrorCode, SshKeyError

# Mirrors kajet_turbo.crypto.ssh_keys.ALGORITHMS -- a type checker requires Literal's
# arguments to be literal values, not that module's constants, so a values-match test
# (tests/api/test_ssh_keys_endpoints.py) keeps the two in sync instead.
SSH_KEY_ALGORITHMS = ("ed25519", "ecdsa-p256", "rsa-4096")


def _require_name(v: str) -> str:
    """Rejects a blank-or-whitespace-only name, present or not (no `min_length` on the
    field -- an empty string and a missing key would otherwise 422 with two different
    codes for the same "no name given" problem: `min_length` fires as generic
    INVALID_INPUT (not declared in legacy_error_codes below), while this validator's
    custom error type maps to the specific SSH_KEY_NAME_REQUIRED). A genuinely *missing*
    key still never reaches this validator (pydantic doesn't run one against an absent
    required field) and falls back to INVALID_INPUT -- "name" stays out of
    legacy_error_codes deliberately, unlike "algorithm" below."""
    stripped = v.strip()
    if not stripped:
        raise PydanticCustomError("ssh_key_name_required", "Name is required")
    return stripped


class CreateSshKeyRequest(RequestModel):
    # REST policy: unknown fields are dropped rather than rejected -- see notes/crud.py.
    model_config = ConfigDict(extra="ignore")

    # "algorithm" is a Literal, not a required string, so a missing key hits the same
    # "missing" branch and a present-but-invalid value hits "literal_error" -- both map
    # back to the legacy SSH_KEY_INVALID_ALGORITHM code here.
    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {
        "algorithm": SshKeyError.INVALID_ALGORITHM
    }

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

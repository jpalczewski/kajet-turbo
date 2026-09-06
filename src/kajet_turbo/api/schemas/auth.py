from pydantic import BaseModel, ConfigDict

from kajet_turbo.api.schemas.preferences import UserPreferences


class SessionResponse(BaseModel):
    email: str
    preferences: UserPreferences


class LoginRequest(BaseModel):
    # REST policy: unknown fields are dropped rather than rejected (MCP's ToolInput
    # keeps extra="forbid" -- an LLM caller benefits from a hard error on a typo, a REST
    # client tolerating an extra field does not).
    model_config = ConfigDict(extra="ignore")

    email: str
    password: str
    pending_id: str | None = None


class LoginResponse(BaseModel):
    email: str
    redirect_uri: str | None = None


class OkResponse(BaseModel):
    ok: bool


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pending_id: str


class ConsentResponse(BaseModel):
    redirect_uri: str


class PendingInfoResponse(BaseModel):
    client_name: str

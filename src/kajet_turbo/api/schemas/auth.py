from pydantic import BaseModel

from kajet_turbo.api.schemas.preferences import UserPreferences


class SessionResponse(BaseModel):
    email: str
    preferences: UserPreferences


class LoginResponse(BaseModel):
    email: str
    redirect_uri: str | None = None


class OkResponse(BaseModel):
    ok: bool


class ConsentResponse(BaseModel):
    redirect_uri: str


class PendingInfoResponse(BaseModel):
    client_name: str

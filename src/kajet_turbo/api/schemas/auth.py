from pydantic import BaseModel


class SessionResponse(BaseModel):
    email: str


class LoginResponse(BaseModel):
    email: str
    redirect_uri: str | None = None


class OkResponse(BaseModel):
    ok: bool


class ConsentResponse(BaseModel):
    redirect_uri: str


class PendingInfoResponse(BaseModel):
    client_name: str

from starlette.requests import Request

from kajet_turbo.auth import KajetOAuthProvider, create_auth
from kajet_turbo.db import Database
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository

db = Database()
note_repo = NoteRepository(db.engine)
user_repo = UserRepository(db.engine)
session_repo = SessionRepository(db.engine)
workspace_repo = WorkspaceRepository(db.engine)
oauth_repo = OAuthRepository(db.engine)
provider: KajetOAuthProvider = create_auth(oauth_repo)


def get_note_repo() -> NoteRepository:
    return note_repo


def get_user_repo() -> UserRepository:
    return user_repo


def get_session_repo() -> SessionRepository:
    return session_repo


def get_workspace_repo() -> WorkspaceRepository:
    return workspace_repo


def get_oauth_repo() -> OAuthRepository:
    return oauth_repo


def get_provider() -> KajetOAuthProvider:
    return provider


def get_session_user(request: Request) -> dict | None:
    token = request.cookies.get("kajet_session", "")
    return session_repo.get_user(token) if token else None

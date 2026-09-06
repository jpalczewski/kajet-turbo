from .auth import AuthError, SecurityEvent, SecurityReason
from .folders import FolderError
from .git import GitError
from .notes import NoteError
from .preferences import PreferencesError
from .request import RequestError
from .targets import TargetError
from .workspace import WorkspaceError

type ErrorCode = (
    AuthError
    | WorkspaceError
    | NoteError
    | FolderError
    | GitError
    | PreferencesError
    | RequestError
    | TargetError
)

__all__ = [
    "AuthError",
    "ErrorCode",
    "FolderError",
    "GitError",
    "NoteError",
    "PreferencesError",
    "RequestError",
    "SecurityEvent",
    "SecurityReason",
    "TargetError",
    "WorkspaceError",
]

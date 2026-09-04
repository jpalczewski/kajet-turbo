from .auth import AuthError
from .folders import FolderError
from .git import GitError
from .notes import NoteError
from .preferences import PreferencesError
from .workspace import WorkspaceError

type ErrorCode = AuthError | WorkspaceError | NoteError | FolderError | GitError | PreferencesError

__all__ = [
    "AuthError",
    "ErrorCode",
    "FolderError",
    "GitError",
    "NoteError",
    "PreferencesError",
    "WorkspaceError",
]

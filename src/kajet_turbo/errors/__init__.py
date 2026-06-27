from .auth import AuthError
from .folders import FolderError
from .git import GitError
from .notes import NoteError
from .workspace import WorkspaceError

type ErrorCode = AuthError | WorkspaceError | NoteError | FolderError | GitError

__all__ = [
    "AuthError",
    "ErrorCode",
    "FolderError",
    "GitError",
    "NoteError",
    "WorkspaceError",
]

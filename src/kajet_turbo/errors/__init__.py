from .auth import AuthError, SecurityEvent, SecurityReason
from .embedding import EmbeddingProfileError
from .folders import FolderError
from .git import GitError
from .jobs import JobError
from .notes import NoteError
from .preferences import PreferencesError
from .request import RequestError
from .ssh_keys import SshKeyError
from .targets import TargetError
from .workspace import WorkspaceError
from .workspace_remote import WorkspaceRemoteError

type ErrorCode = (
    AuthError
    | WorkspaceError
    | NoteError
    | FolderError
    | GitError
    | JobError
    | PreferencesError
    | RequestError
    | TargetError
    | WorkspaceRemoteError
    | SshKeyError
    | EmbeddingProfileError
)

__all__ = [
    "AuthError",
    "EmbeddingProfileError",
    "ErrorCode",
    "FolderError",
    "GitError",
    "JobError",
    "NoteError",
    "PreferencesError",
    "RequestError",
    "SecurityEvent",
    "SecurityReason",
    "SshKeyError",
    "TargetError",
    "WorkspaceError",
    "WorkspaceRemoteError",
]

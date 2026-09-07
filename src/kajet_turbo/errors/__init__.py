from .auth import AuthError, SecurityEvent, SecurityReason
from .embedding import EmbeddingProfileError
from .folders import FolderError
from .git import GitError
from .jobs import JobError
from .notes import NoteError
from .preferences import PreferencesError
from .request import RequestError
from .share_links import ShareLinkError
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
    | ShareLinkError
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
    "ShareLinkError",
    "SshKeyError",
    "TargetError",
    "WorkspaceError",
    "WorkspaceRemoteError",
]

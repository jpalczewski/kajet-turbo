from enum import StrEnum


class EmbeddingProfileError(StrEnum):
    NOT_FOUND = "EMBEDDING_PROFILE_NOT_FOUND"
    PROBE_FAILED = "EMBEDDING_PROFILE_PROBE_FAILED"

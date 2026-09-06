from enum import StrEnum


class JobError(StrEnum):
    # JobRepository.retry/dismiss collapse "no such job", "belongs to another user", and
    # "wrong status for this action" into a single bool -- the route has no way to tell
    # these apart, so a single code is honest about what the client can actually learn.
    NOT_FOUND = "JOB_NOT_FOUND"

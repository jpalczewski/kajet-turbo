from enum import StrEnum


class PermanentJobError(Exception):
    """Raised by a job handler when retrying cannot help (e.g. a revoked API key).

    The worker fails the job terminally, skipping the backoff retries, and logs the
    outcome at ERROR so it stands out from ordinary transient-failure retries.
    """


class JobError(StrEnum):
    # JobRepository.retry/dismiss collapse "no such job", "belongs to another user", and
    # "wrong status for this action" into a single bool -- the route has no way to tell
    # these apart, so a single code is honest about what the client can actually learn.
    NOT_FOUND = "JOB_NOT_FOUND"

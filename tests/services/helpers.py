"""Shared helpers for tests/services/ — see tests/CLAUDE.md: "A helper needed by a second
file moves to the suite's helpers.py — it does not get copied."
"""


def make_flaky_write(real_write, *, fail_on_call: int = 2, message: str = "disk full"):
    """A ``write_note_file`` stand-in that raises ``OSError`` on the Nth call, delegating to
    ``real_write`` otherwise.

    Used to pin the #104 acceptance behavior: a write failing partway through a batch must
    roll back every file already written and make no commit.
    """
    calls = {"n": 0}

    def flaky_write(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise OSError(message)
        return real_write(*args, **kwargs)

    return flaky_write

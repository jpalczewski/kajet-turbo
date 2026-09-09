"""Supervisor. Creates and clears the multiproc dir exactly once, then hands off to
uvicorn's own multiprocess supervisor — the topology kajet uses (uvicorn.run(workers=N)).

The __main__ guard is required, not cosmetic: Python 3.14 defaults to forkserver on
Linux, so a child re-imports this module and would re-enter uvicorn.run() without it.
kajet is not exposed to this — uvicorn receives a factory *string* and children import
kajet_turbo.server rather than the module that called uvicorn.run()."""

import os
import sys
from pathlib import Path


def main() -> None:
    d = Path(os.environ["PROMETHEUS_MULTIPROC_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    if os.environ.get("SPIKE_CLEAR", "1") == "1":
        for f in d.glob("*.db"):
            f.unlink()
    import uvicorn

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=int(sys.argv[1]),
        workers=int(sys.argv[2]),
        log_level="warning",
    )


if __name__ == "__main__":
    main()

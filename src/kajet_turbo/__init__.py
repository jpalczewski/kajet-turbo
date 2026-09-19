import os
import sysconfig

# SQLAlchemy's compiled cyextension modules don't declare free-threading
# support, so importing them silently re-enables the GIL. On a free-threaded
# build force the pure-Python fallbacks instead. Must run before anything
# imports sqlalchemy (db.py via sqlmodel), hence the package __init__.
# (tests/conftest.py sets the same variable because test modules may import
# sqlalchemy before this package.)
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

# prometheus_client resolves its storage backend once, at import, from this variable, so
# it must be in the environment before anything imports the client (docs/specs/
# metrics-multiprocess.md §4). api/mcp run N uvicorn children behind one scrape target and
# need the shared-memory-map mode; worker and all stay single-process. The directory is
# created and cleared once by the supervisor in server.main(). Never import
# prometheus_client here: alembic and the CLI subcommands import this package too.
if os.environ.get("KAJET_ROLE") in {"api", "mcp"}:
    os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp/kajet-prometheus")

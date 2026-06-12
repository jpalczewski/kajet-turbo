import os
import sysconfig
import tempfile

# Some test modules import sqlalchemy before kajet_turbo, bypassing the guard
# in kajet_turbo/__init__.py — set it here too (conftest runs first), so the
# GIL stays disabled on free-threaded builds.
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

# kajet_turbo.dependencies runs Database() and create_auth() at module level on import.
# Both need env vars set before test files are collected by pytest.
if "DB_PATH" not in os.environ:
    _db_fd, _db_path = tempfile.mkstemp(suffix=".db")
    os.close(_db_fd)  # SQLite reopens the (empty) file itself
    os.environ["DB_PATH"] = _db_path
if "MCP_BASE_URL" not in os.environ:
    os.environ["MCP_BASE_URL"] = "http://localhost:8000"

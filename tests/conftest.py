import os
import tempfile

# kajet_turbo.dependencies runs Database() and create_auth() at module level on import.
# Both need env vars set before test files are collected by pytest.
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
if "MCP_BASE_URL" not in os.environ:
    os.environ["MCP_BASE_URL"] = "http://localhost:8000"

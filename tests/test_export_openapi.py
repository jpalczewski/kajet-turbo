import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent


def test_export_openapi_creates_valid_schema(tmp_path):
    result = subprocess.run(
        ["uv", "run", "scripts/export_openapi.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"

    output_file = PROJECT_ROOT / "openapi.json"
    assert output_file.exists(), "openapi.json was not created"

    schema = json.loads(output_file.read_text())
    assert schema["openapi"].startswith("3."), "Not a valid OpenAPI 3.x schema"
    assert "paths" in schema, "Schema has no paths"
    assert len(schema["paths"]) > 0, "Schema has 0 paths"
    assert any("/api/workspaces" in p for p in schema["paths"]), (
        f"Expected /api/workspaces in paths, got: {list(schema['paths'].keys())}"
    )

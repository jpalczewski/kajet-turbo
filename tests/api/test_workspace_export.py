import io
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest


def _save_export_note(auth_client) -> tuple[Path, bytes]:
    _, note_service, workspace = auth_client
    note_service.save("u1", "test-ws", workspace, "Export me", "committed content", [])
    note_path = Path(workspace) / "Export me.md"
    committed = note_path.read_bytes()
    note_path.write_text("uncommitted content")
    (Path(workspace) / "untracked.md").write_text("do not export")
    return note_path, committed


def test_export_zip_uses_head_snapshot(auth_client):
    client, _, _ = auth_client
    _, committed = _save_export_note(auth_client)

    response = client.get("/api/workspaces/test-ws/export?format=zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert re.fullmatch(
        r'attachment; filename="test-ws-[0-9a-f]{12}\.zip"',
        response.headers["content-disposition"],
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        note_name = next(name for name in names if name.endswith("/Export me.md"))
        assert archive.read(note_name) == committed
        assert all("/.git/" not in name for name in names)
        assert all(not name.endswith("/untracked.md") for name in names)


def test_export_tar_zst_uses_head_snapshot(auth_client):
    client, _, _ = auth_client
    _, committed = _save_export_note(auth_client)

    response = client.get("/api/workspaces/test-ws/export?format=tar.zst")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zstd"
    with tarfile.open(None, "r:zst", io.BytesIO(response.content)) as archive:
        member = next(item for item in archive.getmembers() if item.name.endswith("/Export me.md"))
        extracted = archive.extractfile(member)
        assert extracted is not None
        assert extracted.read() == committed


@pytest.mark.parametrize("format", ["zip", "tar.zst"])
def test_export_empty_workspace_returns_an_empty_archive(auth_client, format):
    response = auth_client.get(f"/api/workspaces/test-ws/export?format={format}")

    assert response.status_code == 200
    if format == "zip":
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.namelist() == ["test-ws-empty/"]
    else:
        with tarfile.open(None, "r:zst", io.BytesIO(response.content)) as archive:
            assert [item.name for item in archive.getmembers()] == ["test-ws-empty"]


def test_export_bundle_is_cloneable_with_history(auth_client, tmp_path):
    client, note_service, workspace = auth_client
    note_service.save("u1", "test-ws", workspace, "First", "one", [])
    note_service.save("u1", "test-ws", workspace, "Second", "two", [])

    response = client.get("/api/workspaces/test-ws/export?format=bundle")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-git-bundle"
    bundle = tmp_path / "test-ws.bundle"
    bundle.write_bytes(response.content)
    subprocess.run(["git", "bundle", "verify", str(bundle)], check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(bundle), str(clone)], check=True, capture_output=True)
    assert (clone / "First.md").exists()
    assert (clone / "Second.md").exists()
    history = subprocess.run(
        ["git", "-C", str(clone), "rev-list", "--count", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(history.stdout) == 2


@pytest.mark.parametrize(
    ("client_fixture", "expected_status"),
    [("anon_client", 401), ("no_access_client", 403)],
)
def test_export_requires_authorized_workspace(request, client_fixture, expected_status):
    client = request.getfixturevalue(client_fixture)

    assert client.get("/api/workspaces/test-ws/export").status_code == expected_status


def test_export_rejects_unknown_format(auth_client):
    response = auth_client.get("/api/workspaces/test-ws/export?format=rar")

    assert response.status_code == 422

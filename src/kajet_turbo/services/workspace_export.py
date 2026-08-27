"""Create downloadable, point-in-time exports of a workspace Git repository."""

import os
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from kajet_turbo.repositories.git import GitError, GitRepository, GitSnapshot

_FORMATS = {"zip", "tar.zst", "bundle"}


@dataclass(frozen=True)
class WorkspaceExport:
    path: Path
    filename: str
    media_type: str


def _archive_root(workspace: str, sha: str) -> str:
    safe_name = "".join(char if char.isalnum() or char in "._-" else "-" for char in workspace)
    return f"{safe_name}-{sha[:12]}"


def _zip_timestamp(timestamp: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(timestamp, UTC)
    # ZIP's DOS timestamps cannot represent dates before 1980.
    return (max(value.year, 1980), value.month, value.day, value.hour, value.minute, value.second)


class WorkspaceExportService:
    def create(self, workspace: str, ws_path: str, format: str) -> WorkspaceExport:
        if format not in _FORMATS:
            raise ValueError(f"Unsupported export format: {format}")

        repo = GitRepository(ws_path)
        suffix, media_type = self._format_details(format)
        fd, temp_name = tempfile.mkstemp(prefix="kajet-export-", suffix=suffix)
        os.close(fd)
        output = Path(temp_name)

        try:
            snapshot = repo.head_snapshot()
            root = _archive_root(workspace, snapshot.sha if snapshot else "empty")
            if format == "zip":
                self._write_zip(repo, snapshot, root, output)
            elif format == "tar.zst":
                self._write_tar_zst(repo, snapshot, root, output)
            else:
                snapshot = self._write_bundle(repo, ws_path, output)
                root = _archive_root(workspace, snapshot.sha)
        except Exception:
            output.unlink(missing_ok=True)
            raise

        return WorkspaceExport(
            path=output,
            filename=f"{root}{suffix}",
            media_type=media_type,
        )

    @staticmethod
    def _format_details(format: str) -> tuple[str, str]:
        match format:
            case "zip":
                return ".zip", "application/zip"
            case "tar.zst":
                return ".tar.zst", "application/zstd"
            case "bundle":
                return ".bundle", "application/x-git-bundle"
        raise AssertionError(f"Unknown format: {format}")

    @staticmethod
    def _write_zip(
        repo: GitRepository, snapshot: GitSnapshot | None, root: str, output: Path
    ) -> None:
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            if snapshot is None:
                archive.writestr(f"{root}/", b"")
                return
            timestamp = _zip_timestamp(snapshot.timestamp)

            def write_file(relative_path: str, data: bytes, mode: int) -> None:
                info = zipfile.ZipInfo(f"{root}/{relative_path}", date_time=timestamp)
                info.external_attr = mode << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)

            repo.write_snapshot_files(snapshot, write_file)

    @staticmethod
    def _write_tar_zst(
        repo: GitRepository, snapshot: GitSnapshot | None, root: str, output: Path
    ) -> None:
        with tarfile.open(output, "w:zst") as archive:
            if snapshot is None:
                directory = tarfile.TarInfo(root)
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                archive.addfile(directory)
                return

            def write_file(relative_path: str, data: bytes, mode: int) -> None:
                info = tarfile.TarInfo(f"{root}/{relative_path}")
                info.size = len(data)
                info.mode = mode & 0o777
                info.mtime = snapshot.timestamp
                archive.addfile(info, BytesIO(data))

            repo.write_snapshot_files(snapshot, write_file)

    @staticmethod
    def _write_bundle(repo: GitRepository, ws_path: str, output: Path) -> GitSnapshot:
        # Bundle reads refs and objects together; serialize it with writers so the
        # exported ref set is a coherent repository state.
        with repo.transaction():
            snapshot = repo.head_snapshot()
            if snapshot is None:
                raise GitError("workspace has no commits")
            try:
                subprocess.run(
                    ["git", "-C", ws_path, "bundle", "create", str(output), "--all"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                detail = e.stderr.strip() or e.stdout.strip() or str(e)
                raise GitError(detail) from e
        return snapshot

from dulwich import porcelain

from kajet_turbo.maintenance import migrate_workspaces_to_main
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.git_push import current_branch


def _master_repo(path):
    porcelain.init(str(path))
    (path / "n.md").write_text("x")
    porcelain.add(str(path), paths=["n.md"])
    porcelain.commit(str(path), message=b"c", author=b"t <t@t>", committer=b"t <t@t>")


def test_migrate_walks_user_workspace_repos(tmp_path):
    (tmp_path / "u1").mkdir()
    (tmp_path / "u2").mkdir()
    _master_repo(tmp_path / "u1" / "ws1")  # on master
    _master_repo(tmp_path / "u2" / "ws2")  # on master
    main_ws = tmp_path / "u1" / "ws3"
    main_ws.mkdir()
    GitRepository.init(str(main_ws))  # already main
    (main_ws / "n.md").write_text("x")
    GitRepository(str(main_ws)).commit_file("n.md", "c")

    migrated = migrate_workspaces_to_main(str(tmp_path))

    assert set(migrated) == {str(tmp_path / "u1" / "ws1"), str(tmp_path / "u2" / "ws2")}
    assert current_branch(str(tmp_path / "u1" / "ws1")) == b"refs/heads/main"
    assert current_branch(str(tmp_path / "u2" / "ws2")) == b"refs/heads/main"

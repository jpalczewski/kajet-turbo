import pytest

from kajet_turbo.repositories.git import GitRepository


@pytest.fixture
def git_ws(tmp_path, git_workspace_factory):
    git_workspace_factory(".")
    return GitRepository(str(tmp_path))

from sqlalchemy import inspect

from kajet_turbo.db import Database


def test_workspace_remotes_columns(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("workspace_remotes")}
    assert cols == {
        "user_id",
        "workspace",
        "origin_url",
        "ssh_key_id",
        "enabled",
        "dirty_at",
        "pushed_at",
        "last_error",
        "updated_at",
    }


def test_workspace_remotes_ssh_key_fk_restrict(database: Database):
    fks = inspect(database.engine).get_foreign_keys("workspace_remotes")
    ssh_fk = next(fk for fk in fks if fk["referred_table"] == "ssh_keys")
    assert ssh_fk["constrained_columns"] == ["ssh_key_id"]
    assert (ssh_fk.get("options") or {}).get("ondelete", "").upper() == "RESTRICT"

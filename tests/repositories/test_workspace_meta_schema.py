from sqlalchemy import inspect

from kajet_turbo.db import Database


def test_workspace_meta_table_exists(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("workspace_meta")}
    assert cols == {"user_id", "workspace", "description", "folder", "tags", "updated_at"}

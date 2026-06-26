from sqlalchemy import inspect

from kajet_turbo.db import Database


def test_ssh_keys_table_columns(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("ssh_keys")}
    assert cols == {
        "id",
        "user_id",
        "name",
        "algorithm",
        "public_key",
        "private_key_enc",
        "fingerprint",
        "created_at",
    }


def test_ssh_keys_unique_user_name(database: Database):
    ucs = inspect(database.engine).get_unique_constraints("ssh_keys")
    assert any(set(uc["column_names"]) == {"user_id", "name"} for uc in ucs)

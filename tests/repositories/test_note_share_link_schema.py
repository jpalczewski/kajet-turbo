from sqlalchemy import inspect

from kajet_turbo.db import Database


def test_note_share_links_table_columns(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("note_share_links")}
    assert cols == {
        "token",
        "note_id",
        "workspace",
        "owner_id",
        "created_at",
        "revoked_at",
        "preview_description",
    }


def test_note_share_links_foreign_keys(database: Database):
    fks = inspect(database.engine).get_foreign_keys("note_share_links")
    targets = {(tuple(fk["constrained_columns"]), fk["referred_table"]) for fk in fks}
    assert (("note_id",), "notes") in targets
    assert (("owner_id",), "users") in targets


def test_note_share_links_indexes(database: Database):
    indexes = inspect(database.engine).get_indexes("note_share_links")
    indexed_columns = {tuple(ix["column_names"]) for ix in indexes}
    assert ("note_id",) in indexed_columns
    assert ("owner_id",) in indexed_columns


def test_note_share_link_visits_table_columns_foreign_key_and_index(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("note_share_link_visits")}
    assert cols == {"id", "token", "ip", "user_agent", "created_at", "kind"}

    fks = inspect(database.engine).get_foreign_keys("note_share_link_visits")
    targets = {(tuple(fk["constrained_columns"]), fk["referred_table"]) for fk in fks}
    assert (("token",), "note_share_links") in targets

    indexes = inspect(database.engine).get_indexes("note_share_link_visits")
    assert ("token", "created_at") in {tuple(ix["column_names"]) for ix in indexes}

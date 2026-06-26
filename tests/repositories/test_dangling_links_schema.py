from sqlalchemy import inspect, text
from sqlmodel import Session

from kajet_turbo.db import Database


def test_dangling_links_table_columns(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("dangling_links")}
    assert cols == {
        "id",
        "workspace",
        "owner_id",
        "source_note_id",
        "target_folder",
        "target_title",
        "created_at",
    }


def test_dangling_links_indexes_exist(database: Database):
    names = {ix["name"] for ix in inspect(database.engine).get_indexes("dangling_links")}
    assert "ix_dangling_target" in names
    assert "ix_dangling_source" in names


def test_dangling_target_index_columns(database: Database):
    indexes = {ix["name"]: ix for ix in inspect(database.engine).get_indexes("dangling_links")}
    target_idx = indexes["ix_dangling_target"]
    assert set(target_idx["column_names"]) == {
        "workspace",
        "owner_id",
        "target_folder",
        "target_title",
    }


def test_dangling_source_index_column(database: Database):
    indexes = {ix["name"]: ix for ix in inspect(database.engine).get_indexes("dangling_links")}
    source_idx = indexes["ix_dangling_source"]
    assert source_idx["column_names"] == ["source_note_id"]


def test_dangling_links_in_sqlite_master(database: Database):
    with Session(database.engine) as session:
        result = session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='dangling_links'")
        ).scalar_one_or_none()
    assert result == "dangling_links"

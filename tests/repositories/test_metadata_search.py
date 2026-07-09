from sqlmodel import Session

from kajet_turbo.models import Note, NoteTag, Tag
from kajet_turbo.repositories.notes import NoteRepository


def _note(
    note_id: str, title: str, folder: str, updated_at: str = "2026-01-01T00:00:00+00:00"
) -> Note:
    return Note(
        id=note_id,
        workspace="ws",
        owner_id="u1",
        title=title,
        folder=folder,
        created_at=updated_at,
        updated_at=updated_at,
    )


def test_search_metadata_matches_tag_and_folder_not_title(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        session.add(_note("n1", "Rozmowa 12.03", "książki/Angelika"))
        session.add(
            Tag(
                id="t1",
                workspace="ws",
                owner_id="u1",
                path="angelika",
                name="angelika",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        # Flush so the referenced rows exist before the FK-checked NoteTag insert —
        # SQLAlchemy's ORM only orders inserts via relationship(), not raw FK columns,
        # so without this the note_tags insert can race ahead of tags (see the same
        # pattern in NoteTagRepository._ensure_tag).
        session.flush()
        session.add(NoteTag(note_id="n1", tag_id="t1", source="frontmatter"))
        session.commit()

    hits = repo.search_metadata("ws", "u1", "angelika")
    assert [h["note_id"] for h in hits] == ["n1"]
    assert hits[0]["matched_on"] == ["folder", "tag"]


def test_search_metadata_matches_folder_path_only(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        session.add(_note("n1", "Rozmowa", "książki/Angelika"))
        session.add(_note("n2", "Inna notatka", ""))
        session.commit()
    hits = repo.search_metadata("ws", "u1", "angelika")
    assert [h["note_id"] for h in hits] == ["n1"]
    assert hits[0]["matched_on"] == ["folder"]


def test_search_metadata_unicode_casefold(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        session.add(_note("n1", "Żółta kartka", ""))
        session.commit()
    # ASCII-lowercase query must still match the Polish diacritic title via casefold —
    # SQLite's own lower()/LIKE would miss this (ASCII-only case folding).
    hits = repo.search_metadata("ws", "u1", "żółta")
    assert [h["note_id"] for h in hits] == ["n1"]
    assert hits[0]["matched_on"] == ["title"]


def test_search_metadata_all_tokens_required(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        session.add(_note("n1", "Angelika telefon", ""))
        session.add(_note("n2", "Angelika", ""))
        session.commit()
    hits = repo.search_metadata("ws", "u1", "angelika telefon")
    assert [h["note_id"] for h in hits] == ["n1"]


def test_search_metadata_ranks_exact_title_above_newer_partial_match(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        session.add(_note("n1", "Angelika i telefon", "", updated_at="2026-01-05T00:00:00+00:00"))
        session.add(_note("n2", "Angelika", "", updated_at="2026-01-01T00:00:00+00:00"))
        session.commit()
    hits = repo.search_metadata("ws", "u1", "angelika")
    assert [h["note_id"] for h in hits] == ["n2", "n1"]


def test_search_metadata_ties_break_by_updated_at_desc(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        session.add(_note("n1", "Angelika projekt A", "", updated_at="2026-01-01T00:00:00+00:00"))
        session.add(_note("n2", "Angelika projekt B", "", updated_at="2026-01-10T00:00:00+00:00"))
        session.commit()
    hits = repo.search_metadata("ws", "u1", "angelika")
    assert [h["note_id"] for h in hits] == ["n2", "n1"]


def test_search_metadata_owner_scoped(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        session.add(_note("n1", "Angelika", ""))
        session.commit()
    assert repo.search_metadata("ws", "other-owner", "angelika") == []


def test_search_metadata_respects_limit(database):
    repo = NoteRepository(database.engine)
    with Session(database.engine) as session:
        for i in range(5):
            session.add(
                _note(f"n{i}", f"Angelika {i}", "", updated_at=f"2026-01-0{i + 1}T00:00:00+00:00")
            )
        session.commit()
    assert len(repo.search_metadata("ws", "u1", "angelika", limit=3)) == 3


def test_search_metadata_blank_query_returns_empty(database):
    repo = NoteRepository(database.engine)
    assert repo.search_metadata("ws", "u1", "   ") == []

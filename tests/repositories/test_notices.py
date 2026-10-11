import pytest
from sqlmodel import Session, select

from kajet_turbo.db import Database
from kajet_turbo.models import Notice
from kajet_turbo.repositories.notices import NoticeNotFoundError, NoticeRepository
from tests.conftest import seed_user

KEY = "embedding_auth:p1"


@pytest.fixture
def repo(database: Database) -> NoticeRepository:
    seed_user(database, "u1")
    seed_user(database, "u2")
    return NoticeRepository(database.engine)


def _raise(repo, now, *, user_id="u1", key=KEY):
    with repo.timed_session() as session:
        raised = repo.raise_in_session(session, user_id, "embedding_auth", key, "{}", now)
        session.commit()
    return raised


def _resolve(repo, now, *, user_id="u1", key=KEY):
    with repo.timed_session() as session:
        resolved = repo.resolve_in_session(session, user_id, key, now)
        session.commit()
    return resolved


def _acknowledge(repo, notice_id, now, *, user_id="u1"):
    with repo.timed_session() as session:
        changed = repo.acknowledge_in_session(session, user_id, notice_id, now)
        session.commit()
    return changed


def _rows(database) -> list[Notice]:
    with Session(database.engine) as session:
        return list(session.exec(select(Notice)).all())


def test_repeated_raise_is_one_row_counting_occurrences(repo, database):
    raised = [_raise(repo, now=1000.0 + i) for i in range(200)]

    (row,) = _rows(database)
    assert row.count == 200
    assert row.first_seen_at == 1000.0
    assert row.last_seen_at == 1199.0
    assert {r.id for r in raised} == {row.id}
    assert [r.opened for r in raised] == [True] + [False] * 199


def test_resolve_hides_notice_and_is_one_shot(repo):
    _raise(repo, now=1.0)

    assert _resolve(repo, now=2.0) is True
    assert repo.list_active("u1") == []
    assert repo.find_active_id("u1", KEY) is None
    assert _resolve(repo, now=3.0) is False


def test_resolve_of_never_raised_key_is_a_no_op(repo, database):
    assert _resolve(repo, now=1.0) is False
    assert _rows(database) == []


def test_raise_after_resolve_reopens_same_row_as_new_episode(repo, database):
    first = _raise(repo, now=1.0)
    _raise(repo, now=2.0)
    _acknowledge(repo, first.id, now=3.0)
    with Session(database.engine) as session:
        row = session.get(Notice, first.id)
        assert row is not None
        row.last_delivered_at = 4.0
        session.add(row)
        session.commit()
    _resolve(repo, now=5.0)

    reopened = _raise(repo, now=6.0)

    assert reopened.id == first.id
    assert reopened.opened is True
    (row,) = _rows(database)
    assert (row.count, row.first_seen_at, row.last_seen_at) == (1, 6.0, 6.0)
    assert row.resolved_at is None
    assert row.acknowledged_at is None
    assert row.last_delivered_at is None


def test_bump_keeps_acknowledgement(repo, database):
    raised = _raise(repo, now=1.0)
    _acknowledge(repo, raised.id, now=2.0)

    _raise(repo, now=3.0)

    (row,) = _rows(database)
    assert row.acknowledged_at == 2.0
    assert row.count == 2


def test_same_key_is_separate_per_user(repo):
    a = _raise(repo, now=1.0, user_id="u1")
    b = _raise(repo, now=1.0, user_id="u2")

    assert a.id != b.id
    assert b.opened is True
    assert [n.id for n in repo.list_active("u1")] == [a.id]
    assert [n.id for n in repo.list_active("u2")] == [b.id]


def test_acknowledge_rejects_another_users_notice_and_changes_nothing(repo, database):
    raised = _raise(repo, now=1.0, user_id="u1")

    with pytest.raises(NoticeNotFoundError, match="notice_id"):
        _acknowledge(repo, raised.id, now=2.0, user_id="u2")

    (row,) = _rows(database)
    assert row.acknowledged_at is None


@pytest.mark.parametrize("notice_id", ["missing", ""])
def test_acknowledge_rejects_unknown_id(repo, notice_id):
    with pytest.raises(NoticeNotFoundError):
        _acknowledge(repo, notice_id, now=1.0)


def test_acknowledge_rejects_resolved_notice(repo, database):
    raised = _raise(repo, now=1.0)
    _resolve(repo, now=2.0)

    with pytest.raises(NoticeNotFoundError):
        _acknowledge(repo, raised.id, now=3.0)
    assert _rows(database)[0].acknowledged_at is None


def test_acknowledge_twice_keeps_first_timestamp(repo, database):
    raised = _raise(repo, now=1.0)

    assert _acknowledge(repo, raised.id, now=2.0) is True
    assert _acknowledge(repo, raised.id, now=3.0) is False
    assert _rows(database)[0].acknowledged_at == 2.0


def test_list_active_includes_acknowledged_oldest_first(repo):
    older = _raise(repo, now=1.0, key="embedding_auth:old")
    newer = _raise(repo, now=2.0, key="embedding_auth:new")
    _acknowledge(repo, older.id, now=3.0)

    active = repo.list_active("u1")

    assert [n.id for n in active] == [older.id, newer.id]
    assert active[0].acknowledged_at == 3.0
    assert active[1].acknowledged_at is None

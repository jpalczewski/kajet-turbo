"""GET /api/workspaces/{name}/search (#453).

Ranking itself is covered in tests/services/test_chunk_search_service.py; these tests pin
the REST contract: what reaches the service, what comes back, and what is refused.
"""

from pathlib import Path

import pytest

from kajet_turbo.dependencies import get_note_search_service
from kajet_turbo.repositories.notes import ChunkHit
from kajet_turbo.services.notes import SearchOutcome
from kajet_turbo.services.targets import WorkspaceTarget

URL = "/api/workspaces/test-ws/search"


class FakeSearchService:
    """Records what the route forwards to ``search_async`` and returns a canned outcome."""

    def __init__(self, outcome: SearchOutcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    async def search_async(self, query, workspaces, owner_id, limit=10, folder=None, tags=None):
        self.calls.append(
            {
                "query": query,
                "workspaces": workspaces,
                "owner_id": owner_id,
                "limit": limit,
                "folder": folder,
                "tags": tags,
            }
        )
        return self.outcome


_EMPTY = SearchOutcome(results=[], search_mode="keyword_only", degraded_reason=None, has_more=False)


def _use(ctx, outcome: SearchOutcome = _EMPTY) -> FakeSearchService:
    fake = FakeSearchService(outcome)
    ctx.client.app.dependency_overrides[get_note_search_service] = lambda: fake
    return fake


def _save(ctx, title: str, content: str, tags: list[str] | None = None, folder: str = ""):
    ws = WorkspaceTarget(owner_id="u1", name="test-ws", path=Path(ctx.workspace))
    return ctx.note_service.create.save(ws, title, content, tags or [], folder=folder)["note_id"]


def _note_ids(resp) -> set[str]:
    return {hit["note_id"] for hit in resp.json()["results"]}


# --- against the real service (keyword-only: the fixture configures no embedding backend) ---


def test_search_returns_matching_chunks(auth_client):
    note_id = _save(auth_client, "Plans", "# Garden\n\nplant the quokkaberry seeds")
    _save(auth_client, "Other", "# Shopping\n\nmilk and bread")

    resp = auth_client.client.get(URL, params={"q": "quokkaberry"})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["search_mode"], body["degraded_reason"], body["has_more"]) == (
        "keyword_only",
        None,
        False,
    )
    (hit,) = body["results"]
    assert hit["note_id"] == note_id
    assert hit["title"] == "Plans"
    assert hit["header_path"] == ["# Garden"]
    assert "quokkaberry" in hit["content"]
    assert "chunk_id" not in hit


def test_search_without_match_returns_empty_results(auth_client):
    _save(auth_client, "Plans", "plant the quokkaberry seeds")

    resp = auth_client.client.get(URL, params={"q": "zebrafinchword"})

    assert resp.status_code == 200
    assert resp.json()["results"] == []
    assert resp.json()["has_more"] is False


def test_search_folder_narrows_to_folder_and_descendants(auth_client):
    inside = _save(auth_client, "A", "quokkaberry one", folder="Garden")
    nested = _save(auth_client, "B", "quokkaberry two", folder="Garden/Beds")
    _save(auth_client, "C", "quokkaberry three", folder="Kitchen")

    resp = auth_client.client.get(URL, params={"q": "quokkaberry", "folder": "Garden"})

    assert resp.status_code == 200
    assert _note_ids(resp) == {inside, nested}


def test_search_repeated_tags_narrow_with_or(auth_client):
    red = _save(auth_client, "A", "quokkaberry one", tags=["red"])
    blue = _save(auth_client, "B", "quokkaberry two", tags=["blue"])
    _save(auth_client, "C", "quokkaberry three", tags=["green"])

    resp = auth_client.client.get(
        URL, params=[("q", "quokkaberry"), ("tags", "red"), ("tags", "blue")]
    )

    assert resp.status_code == 200
    assert _note_ids(resp) == {red, blue}


def test_search_has_more_when_matches_exceed_limit(auth_client):
    for i in range(3):
        _save(auth_client, f"N{i}", f"quokkaberry number {i}")

    resp = auth_client.client.get(URL, params={"q": "quokkaberry", "limit": 2})

    assert len(resp.json()["results"]) == 2
    assert resp.json()["has_more"] is True


def test_search_invalid_folder_maps_to_422(auth_client):
    resp = auth_client.client.get(URL, params={"q": "quokkaberry", "folder": "../outside"})

    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_FOLDER"


# --- contract against a fake service ---


def test_search_forwards_path_workspace_and_defaults(auth_client):
    fake = _use(auth_client)

    auth_client.client.get(URL, params={"q": "soup"})

    assert fake.calls == [
        {
            "query": "soup",
            "workspaces": ["test-ws"],
            "owner_id": "u1",
            "limit": 10,
            "folder": None,
            "tags": None,
        }
    ]


def test_search_serializes_degraded_keyword_only_result(auth_client):
    hit = ChunkHit(
        chunk_id="internal",
        note_id="n1",
        title="Soup",
        folder="Recipes",
        updated_at="2026-01-02T00:00:00+00:00",
        header_path=["Soup"],
        content="tomato soup",
        score=0.5,
        matched_on=["title"],
    )
    _use(auth_client, SearchOutcome([hit], "keyword_only", "auth_failed", has_more=True))

    resp = auth_client.client.get(URL, params={"q": "soup"})

    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {
                "note_id": "n1",
                "title": "Soup",
                "folder": "Recipes",
                "updated_at": "2026-01-02T00:00:00+00:00",
                "header_path": ["Soup"],
                "content": "tomato soup",
                "score": 0.5,
                "matched_on": ["title"],
            }
        ],
        "search_mode": "keyword_only",
        "degraded_reason": "auth_failed",
        "has_more": True,
    }


def test_search_inaccessible_workspace_is_rejected_before_search(no_access_client):
    fake = _use(no_access_client)

    resp = no_access_client.client.get(URL, params={"q": "soup"})

    assert resp.status_code == 403
    assert fake.calls == []


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "soup", "limit": "0"},
        {"q": "soup", "limit": "51"},
        {"q": "soup", "limit": "abc"},
    ],
    ids=["missing-q", "empty-q", "limit-0", "limit-51", "limit-nan"],
)
def test_search_rejects_bad_query_parameters(auth_client, params):
    fake = _use(auth_client)

    resp = auth_client.client.get(URL, params=params)

    assert resp.status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize("limit", [1, 50])
def test_search_accepts_limit_bounds(auth_client, limit):
    fake = _use(auth_client)

    resp = auth_client.client.get(URL, params={"q": "soup", "limit": limit})

    assert resp.status_code == 200
    assert fake.calls[0]["limit"] == limit

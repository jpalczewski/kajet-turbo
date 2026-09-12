from datetime import date


def _define(service, workspace, *, name, grain, cardinality, folder, title):
    return service.define_collection(
        str(workspace), "test-ws", "u1", name, grain, cardinality, folder, title
    )


def test_list_collections_returns_definitions_for_each_grain(auth_client):
    client, _, workspace = auth_client
    collection_service = auth_client.collection_service
    definitions = [
        ("daily", "day", "one", "daily/{year}/{month}", "{date}"),
        ("weekly", "week", "one", "weekly/{year}", "{key}"),
        ("monthly", "month", "one", "monthly/{year}", "{key}"),
        ("yearly", "year", "one", "yearly", "{key}"),
    ]
    for name, grain, cardinality, folder, title in definitions:
        _define(
            collection_service,
            workspace,
            name=name,
            grain=grain,
            cardinality=cardinality,
            folder=folder,
            title=title,
        )

    response = client.get("/api/workspaces/test-ws/collections")

    assert response.status_code == 200
    assert response.json() == {
        "collections": [
            {
                "name": name,
                "grain": grain,
                "cardinality": cardinality,
                "folder": folder,
                "title": title,
                "description": None,
            }
            for name, grain, cardinality, folder, title in definitions
        ]
    }


def test_list_collection_entries_returns_all_many_members_without_file_stats(auth_client):
    client, _, workspace = auth_client
    collection_service = auth_client.collection_service
    _define(
        collection_service,
        workspace,
        name="sessions",
        grain="day",
        cardinality="many",
        folder="sessions/{year}/{month}",
        title="{date} {ordinal}",
    )
    first = collection_service.open_entry(
        str(workspace), "test-ws", "u1", "sessions", date(2026, 6, 15)
    )
    second = collection_service.open_entry(
        str(workspace), "test-ws", "u1", "sessions", date(2026, 6, 15)
    )
    client.post(
        "/api/workspaces/test-ws/notes",
        json={"title": "Not a session", "folder": "sessions/2026/06"},
    )

    response = client.get("/api/workspaces/test-ws/collections/sessions/entries")

    assert response.status_code == 200
    entries = response.json()["notes"]
    assert {entry["note_id"] for entry in entries} == {first["note_id"], second["note_id"]}
    assert all("size_bytes" not in entry for entry in entries)
    assert {entry["occurred_at"] for entry in entries} == {"2026-06-15"}


def test_list_collection_entries_unknown_collection_returns_error_code(auth_client):
    response = auth_client.get("/api/workspaces/test-ws/collections/missing/entries")

    assert response.status_code == 404
    assert response.json()["error"] == "COLLECTION_NOT_FOUND"


def test_list_collection_entries_excludes_non_member_in_collection_folder(auth_client):
    client, _, workspace = auth_client
    collection_service = auth_client.collection_service
    _define(
        collection_service,
        workspace,
        name="journal",
        grain="day",
        cardinality="one",
        folder="journal/{year}/{month}",
        title="{date}",
    )
    member = collection_service.open_entry(
        str(workspace), "test-ws", "u1", "journal", date(2026, 6, 15)
    )
    client.post(
        "/api/workspaces/test-ws/notes",
        json={"title": "Not a date", "folder": "journal/2026/06"},
    )

    response = client.get("/api/workspaces/test-ws/collections/journal/entries")

    assert response.status_code == 200
    assert [entry["note_id"] for entry in response.json()["notes"]] == [member["note_id"]]

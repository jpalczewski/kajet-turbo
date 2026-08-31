def test_entries_in_filters_occurred_at_and_folder(auth_client):
    client, service, workspace = auth_client
    wanted = service.save(
        "u1",
        "test-ws",
        workspace,
        "Wanted",
        "",
        [],
        folder="journal/2026",
        occurred_at="2026-03-22",
    )
    service.save(
        "u1",
        "test-ws",
        workspace,
        "Sibling",
        "",
        [],
        folder="journals-old",
        occurred_at="2026-03-22",
    )
    service.save("u1", "test-ws", workspace, "Summary", "", [], period="2026-W12")

    response = client.get(
        "/api/workspaces/test-ws/entries", params={"period": "2026-W12", "folder": "journal"}
    )

    assert response.status_code == 200
    assert [item["note_id"] for item in response.json()["notes"]] == [wanted["note_id"]]


def test_entries_in_rejects_invalid_period(auth_client):
    client, _, _ = auth_client
    response = client.get("/api/workspaces/test-ws/entries", params={"period": "nope"})
    assert response.status_code == 422


def test_entries_in_matches_period_notes_by_overlap(auth_client):
    client, service, workspace = auth_client
    week = service.save("u1", "test-ws", workspace, "Week", "", [], period="2026-W12")
    month = service.save("u1", "test-ws", workspace, "Month", "", [], period="2026-03")
    year = service.save("u1", "test-ws", workspace, "Year", "", [], period="2026")
    other_month = service.save("u1", "test-ws", workspace, "Other month", "", [], period="2026-04")

    response = client.get("/api/workspaces/test-ws/entries", params={"period": "2026-03-16"})

    assert response.status_code == 200
    ids = {item["note_id"] for item in response.json()["notes"]}
    assert ids == {week["note_id"], month["note_id"], year["note_id"]}
    assert other_month["note_id"] not in ids

from pathlib import Path


def test_html_returns_rendered_content(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save(
        "u1", "test-ws", ws_path, "Testowa notatka", "# Nagłówek\n\nAkapit.", ["python"]
    )["note_id"]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")

    assert resp.status_code == 200
    data = resp.json()
    assert data["note_id"] == note_id
    assert data["title"] == "Testowa notatka"
    assert data["tags"] == ["python"]
    assert "<h1>" in data["content_html"]
    assert "Nagłówek" in data["content_html"]
    assert "Akapit" in data["content_html"]
    assert "content" not in data


def test_html_returns_401_when_not_logged_in(anon_client):
    resp = anon_client.get("/api/workspaces/test-ws/notes/abc1234/html")
    assert resp.status_code == 401


def test_html_returns_403_when_no_access(no_access_client):
    resp = no_access_client.get("/api/workspaces/test-ws/notes/abc1234/html")
    assert resp.status_code == 403


def test_html_returns_404_when_note_missing(auth_client):
    client, _, _ = auth_client
    resp = client.get("/api/workspaces/test-ws/notes/nonexistent/html")
    assert resp.status_code == 404


def test_markdown_returns_raw_content(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "MD notatka", "# Hello\n\nŚwiat.", [])[
        "note_id"
    ]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/markdown")

    assert resp.status_code == 200
    data = resp.json()
    assert data["note_id"] == note_id
    assert data["title"] == "MD notatka"
    assert data["content"] == "# Hello\n\nŚwiat."
    assert "content_html" not in data


def test_markdown_returns_401_when_not_logged_in(anon_client):
    resp = anon_client.get("/api/workspaces/test-ws/notes/abc1234/markdown")
    assert resp.status_code == 401


def test_markdown_returns_403_when_no_access(no_access_client):
    resp = no_access_client.get("/api/workspaces/test-ws/notes/abc1234/markdown")
    assert resp.status_code == 403


def test_markdown_returns_404_when_note_missing(auth_client):
    client, _, _ = auth_client
    resp = client.get("/api/workspaces/test-ws/notes/nonexistent/markdown")
    assert resp.status_code == 404


def test_html_strips_script_tags(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save(
        "u1",
        "test-ws",
        ws_path,
        "XSS test",
        "<script>alert(1)</script>\n\n## Bezpieczny nagłówek",
        [],
    )["note_id"]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")

    assert resp.status_code == 200
    html = resp.json()["content_html"]
    assert "<script>" not in html
    assert "</script>" not in html
    assert "Bezpieczny" in html


def test_html_strips_javascript_urls(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save(
        "u1", "test-ws", ws_path, "JS URL test", "[kliknij](javascript:alert(1))", []
    )["note_id"]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")

    assert resp.status_code == 200
    html = resp.json()["content_html"]
    # markdown-it refuses to linkify a javascript: URL, leaving inert literal text rather than a
    # clickable link; the security property is that no executable javascript href is produced.
    assert 'href="javascript:' not in html
    assert "<a" not in html


def test_list_notes_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Lista notatka", "content", [], folder="Projekty")
    resp = client.get("/api/workspaces/test-ws/notes")
    assert resp.status_code == 200
    notes = resp.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["folder"] == "Projekty"


def test_list_notes_folder_filter(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "W folderze", "content", [], folder="A")
    note_svc.save("u1", "test-ws", ws_path, "W rootu", "content", [])
    resp = client.get("/api/workspaces/test-ws/notes?folder=A")
    assert resp.status_code == 200
    notes = resp.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["title"] == "W folderze"


def test_html_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "HTML folder", "treść", [], folder="Docs")[
        "note_id"
    ]
    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")
    assert resp.status_code == 200
    assert resp.json()["folder"] == "Docs"


def test_markdown_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "MD folder", "treść", [], folder="Arch")[
        "note_id"
    ]
    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/markdown")
    assert resp.status_code == 200
    assert resp.json()["folder"] == "Arch"


def test_list_notes_includes_size_bytes(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Sized Note", "hello world", [])
    resp = client.get("/api/workspaces/test-ws/notes")
    assert resp.status_code == 200
    note = resp.json()["notes"][0]
    assert "size_bytes" in note
    assert isinstance(note["size_bytes"], int)
    assert note["size_bytes"] > 0


def test_create_note_returns_note_id(auth_client):
    client, _, _ = auth_client
    resp = client.post(
        "/api/workspaces/test-ws/notes",
        json={"title": "Nowa Notatka", "content": "treść", "folder": ""},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "note_id" in data
    assert len(data["note_id"]) > 0


def test_create_note_in_subfolder(auth_client):
    client, note_svc, ws_path = auth_client
    resp = client.post(
        "/api/workspaces/test-ws/notes",
        json={"title": "Subfolder Note", "content": "", "folder": "docs"},
    )
    assert resp.status_code == 201
    note_id = resp.json()["note_id"]
    note = note_svc.get_with_content(note_id, owner_id="u1", ws_path=ws_path)
    assert note is not None
    assert note["folder"] == "docs"


def test_create_note_duplicate_returns_409(auth_client):
    client, _, _ = auth_client
    client.post("/api/workspaces/test-ws/notes", json={"title": "Dup"})
    resp = client.post("/api/workspaces/test-ws/notes", json={"title": "Dup"})
    assert resp.status_code == 409
    assert "error" in resp.json()


def test_create_note_missing_title_returns_422(auth_client):
    client, _, _ = auth_client
    resp = client.post("/api/workspaces/test-ws/notes", json={"content": "x"})
    assert resp.status_code == 422


def test_create_note_returns_401_when_anon(anon_client):
    resp = anon_client.post("/api/workspaces/test-ws/notes", json={"title": "T"})
    assert resp.status_code == 401


def test_create_note_returns_403_when_no_access(no_access_client):
    resp = no_access_client.post("/api/workspaces/test-ws/notes", json={"title": "T"})
    assert resp.status_code == 403


def test_update_note_content(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Orig", "old content", [])["note_id"]
    resp = client.patch(
        f"/api/workspaces/test-ws/notes/{note_id}",
        json={"content": "new content"},
    )
    assert resp.status_code == 200
    assert resp.json()["note_id"] == note_id
    updated = note_svc.get_with_content(note_id, owner_id="u1", ws_path=ws_path)
    assert updated["content"] == "new content"


def test_update_note_title(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Old Title", "c", [])["note_id"]
    resp = client.patch(
        f"/api/workspaces/test-ws/notes/{note_id}",
        json={"title": "New Title"},
    )
    assert resp.status_code == 200
    updated = note_svc.get(note_id, owner_id="u1")
    assert updated["title"] == "New Title"


def test_move_note_to_existing_folder(auth_client):
    client, note_svc, ws_path = auth_client
    (Path(ws_path) / "archive").mkdir()
    note_id = note_svc.save("u1", "test-ws", ws_path, "Move me", "c", [])["note_id"]

    resp = client.post(f"/api/workspaces/test-ws/notes/{note_id}/move", json={"folder": "archive"})

    assert resp.status_code == 200
    assert resp.json() == {"note_id": note_id, "folder": "archive"}
    assert (Path(ws_path) / "archive" / "Move me.md").exists()


def test_move_note_to_root(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Move me", "c", [], folder="docs")["note_id"]

    resp = client.post(f"/api/workspaces/test-ws/notes/{note_id}/move", json={"folder": ""})

    assert resp.status_code == 200
    assert (Path(ws_path) / "Move me.md").exists()


def test_move_note_creates_missing_folder_path(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Move me", "c", [])["note_id"]

    resp = client.post(
        f"/api/workspaces/test-ws/notes/{note_id}/move", json={"folder": "new/nested"}
    )

    assert resp.status_code == 200
    assert (Path(ws_path) / "new" / "nested" / "Move me.md").exists()


def test_move_note_collision_returns_409(auth_client):
    client, note_svc, ws_path = auth_client
    (Path(ws_path) / "archive").mkdir()
    note_id = note_svc.save("u1", "test-ws", ws_path, "Same", "source", [])["note_id"]
    note_svc.save("u1", "test-ws", ws_path, "Same", "destination", [], folder="archive")

    resp = client.post(f"/api/workspaces/test-ws/notes/{note_id}/move", json={"folder": "archive"})

    assert resp.status_code == 409


def test_move_note_invalid_path_returns_422(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Move me", "c", [])["note_id"]

    resp = client.post(
        f"/api/workspaces/test-ws/notes/{note_id}/move", json={"folder": "../outside"}
    )

    assert resp.status_code == 422


def test_move_note_returns_401_when_anon(anon_client):
    resp = anon_client.post("/api/workspaces/test-ws/notes/abc/move", json={"folder": ""})
    assert resp.status_code == 401


def test_move_note_returns_403_when_no_access(no_access_client):
    resp = no_access_client.post("/api/workspaces/test-ws/notes/abc/move", json={"folder": ""})
    assert resp.status_code == 403


def test_update_note_not_found_returns_404(auth_client):
    client, _, _ = auth_client
    resp = client.patch(
        "/api/workspaces/test-ws/notes/nonexistent",
        json={"content": "x"},
    )
    assert resp.status_code == 404


def test_update_note_returns_401_when_anon(anon_client):
    resp = anon_client.patch("/api/workspaces/test-ws/notes/abc", json={"content": "x"})
    assert resp.status_code == 401


def test_update_note_returns_403_when_no_access(no_access_client):
    resp = no_access_client.patch("/api/workspaces/test-ws/notes/abc", json={"content": "x"})
    assert resp.status_code == 403


def test_delete_note_removes_it(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "To Delete", "c", [])["note_id"]
    resp = client.delete(f"/api/workspaces/test-ws/notes/{note_id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert note_svc.get(note_id, owner_id="u1") is None


def test_delete_note_not_found_returns_404(auth_client):
    client, _, _ = auth_client
    resp = client.delete("/api/workspaces/test-ws/notes/nonexistent")
    assert resp.status_code == 404


def test_delete_note_returns_401_when_anon(anon_client):
    resp = anon_client.delete("/api/workspaces/test-ws/notes/abc")
    assert resp.status_code == 401


def test_delete_note_returns_403_when_no_access(no_access_client):
    resp = no_access_client.delete("/api/workspaces/test-ws/notes/abc")
    assert resp.status_code == 403


def test_create_note_broken_wikilink_returns_422(auth_client):
    client, _, _ = auth_client
    resp = client.post(
        "/api/workspaces/test-ws/notes",
        json={"title": "Source", "content": "see [[Ghost]]"},
    )
    assert resp.status_code == 422
    assert "Ghost" in resp.json()["error"]


def test_create_note_valid_wikilink_succeeds(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Target", "t", [])
    resp = client.post(
        "/api/workspaces/test-ws/notes",
        json={"title": "Source", "content": "see [[Target|t]]"},
    )
    assert resp.status_code == 201


def test_update_note_broken_wikilink_returns_422(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Note", "body", [])["note_id"]
    resp = client.patch(
        f"/api/workspaces/test-ws/notes/{note_id}",
        json={"content": "[[Ghost]]"},
    )
    assert resp.status_code == 422


def test_html_renders_clickable_wikilink(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Target", "t", [], folder="A")
    sid = note_svc.save("u1", "test-ws", ws_path, "Source", "go [[A/Target|here]]", [])["note_id"]
    tid = note_svc._repo.get_by_path("test-ws", "u1", "A", "Target").id

    resp = client.get(f"/api/workspaces/test-ws/notes/{sid}/html")

    assert resp.status_code == 200
    html = resp.json()["content_html"]
    assert f'<a class="wikilink" href="/workspace/test-ws/notes/A/{tid}">here</a>' in html


def test_html_renders_broken_wikilink_when_target_deleted(auth_client):
    client, note_svc, ws_path = auth_client
    tid = note_svc.save("u1", "test-ws", ws_path, "Target", "t", [])["note_id"]
    sid = note_svc.save("u1", "test-ws", ws_path, "Source", "go [[Target]]", [])["note_id"]
    note_svc.delete(tid, owner_id="u1", ws_path=ws_path)

    resp = client.get(f"/api/workspaces/test-ws/notes/{sid}/html")

    assert resp.status_code == 200
    html = resp.json()["content_html"]
    assert '<span class="wikilink-broken">Target</span>' in html


def test_links_returns_backlinks_and_outlinks(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Target", "t", [])
    note_svc.save("u1", "test-ws", ws_path, "Source", "[[Target]]", [])
    tid = note_svc._repo.get_by_path("test-ws", "u1", "", "Target").id
    sid = note_svc._repo.get_by_path("test-ws", "u1", "", "Source").id

    # Target sees Source as a backlink, no outlinks.
    target = client.get(f"/api/workspaces/test-ws/notes/{tid}/links").json()
    assert [b["title"] for b in target["backlinks"]] == ["Source"]
    assert target["outlinks"] == []

    # Source sees Target as an outlink, no backlinks.
    source = client.get(f"/api/workspaces/test-ws/notes/{sid}/links").json()
    assert source["backlinks"] == []
    assert [o["title"] for o in source["outlinks"]] == ["Target"]


def test_links_empty(auth_client):
    client, note_svc, ws_path = auth_client
    tid = note_svc.save("u1", "test-ws", ws_path, "Lonely", "t", [])["note_id"]
    resp = client.get(f"/api/workspaces/test-ws/notes/{tid}/links")
    assert resp.status_code == 200
    assert resp.json() == {"backlinks": [], "outlinks": []}


def test_links_returns_403_when_no_access(no_access_client):
    resp = no_access_client.get("/api/workspaces/test-ws/notes/abc1234/links")
    assert resp.status_code == 403


def test_batch_create_returns_results(auth_client):
    client, _, _ = auth_client
    resp = client.post(
        "/api/workspaces/test-ws/notes/batch",
        json={
            "notes": [
                {"title": "API One", "content": "a"},
                {"title": "API Two", "content": "b", "folder": "docs"},
            ]
        },
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["index"] for r in results] == [0, 1]
    assert all(r["note_id"] for r in results)


def test_batch_create_best_effort_mixed(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", str(ws_path), "Dup", "x", [])
    resp = client.post(
        "/api/workspaces/test-ws/notes/batch",
        json={"notes": [{"title": "Dup", "content": "x"}, {"title": "OK", "content": "y"}]},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["error"]
    assert results[1]["note_id"]


def test_batch_create_empty_notes_422(auth_client):
    client, _, _ = auth_client
    resp = client.post("/api/workspaces/test-ws/notes/batch", json={"notes": []})
    assert resp.status_code == 422


def test_batch_create_401_when_anon(anon_client):
    resp = anon_client.post(
        "/api/workspaces/test-ws/notes/batch",
        json={"notes": [{"title": "X", "content": "y"}]},
    )
    assert resp.status_code == 401


def test_batch_create_403_when_no_access(no_access_client):
    resp = no_access_client.post(
        "/api/workspaces/test-ws/notes/batch",
        json={"notes": [{"title": "X", "content": "y"}]},
    )
    assert resp.status_code == 403


def test_batch_create_missing_notes_key_returns_422(auth_client):
    client, _, _ = auth_client
    resp = client.post("/api/workspaces/test-ws/notes/batch", json={})
    assert resp.status_code == 422


def test_batch_create_malformed_note_missing_title_returns_per_note_error(auth_client):
    client, _, _ = auth_client
    resp = client.post(
        "/api/workspaces/test-ws/notes/batch",
        json={"notes": [{"content": "no title"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["error"]

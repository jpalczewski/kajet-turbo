from kajet_turbo.services.notes.staleness import sha_is_fresh, stale_error, stale_payload


def test_sha_is_fresh_accepts_prefix_match():
    assert sha_is_fresh("abcdef1234", "abcdef1")


def test_sha_is_fresh_rejects_mismatch():
    assert not sha_is_fresh("abcdef1234", "0000000")


def test_sha_is_fresh_rejects_empty_expected():
    assert not sha_is_fresh("abcdef1234", "")
    assert not sha_is_fresh("abcdef1234", "   ")


def test_sha_is_fresh_rejects_missing_current():
    assert not sha_is_fresh(None, "abcdef1")


def test_sha_is_fresh_rejects_none_expected():
    assert not sha_is_fresh("abcdef1234", None)


def test_stale_payload_shape_and_message():
    payload = stale_payload("n1")
    assert payload["stale_sha"] is True
    assert payload["note_id"] == "n1"
    assert payload["error"] == stale_error("n1")
    assert "expected_sha nieaktualny dla n1" in payload["error"]

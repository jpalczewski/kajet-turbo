import pytest

from kajet_turbo import workspace_settings as ws


def test_defaults_includes_validate_links():
    assert ws.defaults() == {"validate_links": True}


def test_definitions_shape():
    defs = ws.definitions()
    assert {d["key"] for d in defs} == {"validate_links"}
    vl = next(d for d in defs if d["key"] == "validate_links")
    assert vl["type"] == "bool"
    assert vl["default"] is True
    assert vl["label"] and vl["description"]


def test_validate_accepts_bool():
    assert ws.validate("validate_links", False) is False
    assert ws.validate("validate_links", True) is True


def test_validate_rejects_unknown_key():
    with pytest.raises(ValueError):
        ws.validate("nope", True)


def test_validate_rejects_wrong_type():
    with pytest.raises(ValueError):
        ws.validate("validate_links", "yes")
    with pytest.raises(ValueError):
        ws.validate("validate_links", 1)  # bool only, no int coercion


def test_coerce_all_fills_missing_with_defaults():
    assert ws.coerce_all(None) == {"validate_links": True}
    assert ws.coerce_all({}) == {"validate_links": True}


def test_coerce_all_drops_unknown_keys():
    assert ws.coerce_all({"validate_links": False, "ghost": 1}) == {"validate_links": False}

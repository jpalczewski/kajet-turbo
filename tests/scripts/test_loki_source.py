import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from loki_source import (
    _to_unix_ns,
    _warn_if_capped,
    build_selector,
    parse_query_range_response,
)


def test_build_selector_base():
    sel = build_selector("mcp", "produkcja")
    assert sel == (
        '{coolify_projectName="kajet-turbo", '
        'container=~"kajet-mcp-.*", '
        'coolify_environmentName="produkcja"}'
    )


def test_build_selector_with_min_level_warning():
    sel = build_selector("mcp", "produkcja", min_level="warning")
    assert sel == (
        '{coolify_projectName="kajet-turbo", '
        'container=~"kajet-mcp-.*", '
        'coolify_environmentName="produkcja", '
        'level=~"warning|error|critical"}'
    )


def test_build_selector_with_msg_filter():
    sel = build_selector("mcp", "produkcja", msg_filter=["save_note", "note_updated"])
    assert sel == (
        '{coolify_projectName="kajet-turbo", '
        'container=~"kajet-mcp-.*", '
        'coolify_environmentName="produkcja", '
        'msg=~"save_note|note_updated"}'
    )


def test_build_selector_env_alias_normalizes():
    # matches analyze-logs.py's existing prod/production -> produkcja, dev/development -> develop
    assert 'coolify_environmentName="produkcja"' in build_selector("mcp", "prod")
    assert 'coolify_environmentName="develop"' in build_selector("mcp", "dev")


def test_parse_query_range_response_flattens_and_sorts():
    data = {
        "data": {
            "result": [
                {
                    "stream": {"container": "kajet-mcp-abc"},
                    "values": [
                        ["1700000002000000000", '{"ts": "2026-01-01T00:00:02Z", "msg": "b"}'],
                        ["1700000000000000000", '{"ts": "2026-01-01T00:00:00Z", "msg": "a"}'],
                    ],
                },
                {
                    "stream": {"container": "kajet-mcp-def"},
                    "values": [
                        ["1700000001000000000", '{"ts": "2026-01-01T00:00:01Z", "msg": "c"}'],
                    ],
                },
            ]
        }
    }
    events = parse_query_range_response(data)
    assert [e["msg"] for e in events] == ["a", "c", "b"]


def test_parse_query_range_response_skips_unparseable_lines():
    data = {
        "data": {
            "result": [
                {
                    "stream": {},
                    "values": [
                        ["1700000000000000000", "not json at all"],
                        ["1700000001000000000", '{"ts": "2026-01-01T00:00:01Z", "msg": "ok"}'],
                    ],
                }
            ]
        }
    }
    events = parse_query_range_response(data)
    assert len(events) == 1
    assert events[0]["msg"] == "ok"


def test_parse_query_range_response_empty_result():
    assert parse_query_range_response({"data": {"result": []}}) == []


def test_to_unix_ns_now_is_close_to_current_time():
    assert abs(_to_unix_ns("now") - int(time.time() * 1e9)) < 2_000_000_000  # within 2s


def test_to_unix_ns_relative_duration():
    now_ns = int(time.time() * 1e9)
    one_hour_ago_ns = _to_unix_ns("1h")
    assert abs((now_ns - one_hour_ago_ns) - 3600 * 1_000_000_000) < 2_000_000_000


def test_to_unix_ns_iso_timestamp():
    assert _to_unix_ns("2026-01-01T00:00:00") == int(
        __import__("datetime").datetime.fromisoformat("2026-01-01T00:00:00").timestamp() * 1e9
    )


def test_warn_if_capped_warns_on_5000_entries(capsys):
    """Verify warning is printed to stderr when result is exactly 5000 entries."""
    events = [{"msg": f"event_{i}"} for i in range(5000)]
    _warn_if_capped(events)
    captured = capsys.readouterr()
    assert "warning: Loki result capped at 5000 entries" in captured.err
    assert "Narrow --since, or add --mode errors" in captured.err


def test_warn_if_capped_no_warning_under_5000(capsys):
    """Verify no warning when result is under 5000 entries."""
    events = [{"msg": f"event_{i}"} for i in range(4999)]
    _warn_if_capped(events)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_warn_if_capped_no_warning_over_5000(capsys):
    """Verify no warning when result is over 5000 entries (shouldn't happen but be safe)."""
    events = [{"msg": f"event_{i}"} for i in range(5001)]
    _warn_if_capped(events)
    captured = capsys.readouterr()
    assert captured.err == ""

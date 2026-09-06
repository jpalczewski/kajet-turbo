import datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import pytest
from loki_source import (
    LokiConfigError,
    SshTarget,
    _entry_timestamps,
    _to_unix_ns,
    _warn_if_capped,
    _warn_if_stale,
    build_selector,
    parse_query_range_response,
)

SSH_ENV = ("KAJET_LOG_SSH_HOST", "KAJET_LOG_SSH_USER", "KAJET_LOG_SSH_KEY")


def test_build_selector_base():
    sel = build_selector("mcp", "produkcja")
    assert sel == (
        '{coolify_projectName="kajet-turbo", '
        'service="kajet-mcp", '
        'coolify_environmentName="produkcja"}'
    )


def test_build_selector_with_min_level_warning():
    sel = build_selector("mcp", "produkcja", min_level="warning")
    assert sel == (
        '{coolify_projectName="kajet-turbo", '
        'service="kajet-mcp", '
        'coolify_environmentName="produkcja", '
        'level=~"warning|error|critical"}'
    )


def test_build_selector_with_msg_filter():
    """msg stopped being a Loki label (per-UUID cardinality), so it filters the line."""
    sel = build_selector("mcp", "produkcja", msg_filter=["save_note", "note_updated"])
    assert sel == (
        '{coolify_projectName="kajet-turbo", '
        'service="kajet-mcp", '
        'coolify_environmentName="produkcja"} '
        '| json | msg=~"save_note|note_updated"'
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
        datetime.datetime.fromisoformat("2026-01-01T00:00:00").timestamp() * 1e9
    )


def test_warn_if_capped_warns_on_5000_entries(capsys):
    """Verify warning is printed to stderr when result is exactly 5000 entries."""
    _warn_if_capped(5000)
    captured = capsys.readouterr()
    assert "warning: Loki result capped at 5000 entries" in captured.err
    assert "Narrow --since, or add --mode errors" in captured.err


def test_warn_if_capped_no_warning_under_5000(capsys):
    """Verify no warning when result is under 5000 entries."""
    _warn_if_capped(4999)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_warn_if_capped_no_warning_over_5000(capsys):
    """Verify no warning when result is over 5000 entries (shouldn't happen but be safe)."""
    _warn_if_capped(5001)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_entry_timestamps_count_every_line_not_just_parseable_ones():
    """The cap and freshness checks look at what Loki returned, not what parsed.

    Regression: a production api stream where most lines were raw uvicorn tracebacks
    came back as exactly 5000 entries, parsed to 681 events, and neither the cap
    warning nor an honest freshness check fired — the 24h window had silently shrunk
    to 90 minutes.
    """
    values = [[str(1_000 + i), '{"ts": "2026-01-01T00:00:00Z", "msg": "http"}'] for i in range(10)]
    values += [[str(2_000 + i), '  File "/app/x.py", line 1, in f'] for i in range(40)]
    data = {"data": {"result": [{"stream": {}, "values": values}]}}
    stamps = _entry_timestamps(data)
    assert len(stamps) == 50
    assert stamps == sorted(stamps)
    assert stamps[-1] == 2_039
    assert len(parse_query_range_response(data)) == 10


def _entry_aged(seconds: float) -> int:
    """A Loki entry timestamp (ns) `seconds` behind now."""
    return int((time.time() - seconds) * 1e9)


def test_warn_if_stale_warns_when_newest_entry_lags(capsys):
    _warn_if_stale(_entry_aged(600), "now")
    captured = capsys.readouterr()
    assert "warning: newest Loki event is" in captured.err
    assert "may not match" in captured.err


def test_warn_if_stale_silent_on_fresh_window(capsys):
    _warn_if_stale(_entry_aged(5), "now")
    assert capsys.readouterr().err == ""


def test_warn_if_stale_silent_for_historical_window(capsys):
    """A window ending in the past is expected to lag — only `until=now` implies freshness."""
    _warn_if_stale(_entry_aged(86400), "2026-01-01T00:00:00")
    assert capsys.readouterr().err == ""


def test_warn_if_stale_silent_without_entries(capsys):
    _warn_if_stale(None, "now")
    assert capsys.readouterr().err == ""


def _set_ssh_env(monkeypatch, tmp_path, **overrides: str | None) -> Path:
    """Set the three SSH variables, with `None` meaning "unset this one".

    Also points KAJET_LOG_ENV_FILE at a path under tmp_path that does not exist yet, so
    a real `.env` in the checkout can never decide the outcome of a test. Returns that
    path for the cases that want to write one.
    """
    values = {"KAJET_LOG_SSH_HOST": "logs.example", "KAJET_LOG_SSH_USER": "reader"}
    values["KAJET_LOG_SSH_KEY"] = "/keys/reader"
    values.update(overrides)
    for var in SSH_ENV:
        value = values[var]
        if value is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, value)
    env_file = tmp_path / "loki.env"
    monkeypatch.setenv("KAJET_LOG_ENV_FILE", str(env_file))
    return env_file


def test_ssh_target_from_env_reads_all_three(monkeypatch, tmp_path):
    _set_ssh_env(monkeypatch, tmp_path)
    assert SshTarget.from_env() == SshTarget(host="logs.example", user="reader", key="/keys/reader")


def test_ssh_target_from_env_names_the_single_missing_variable(monkeypatch, tmp_path):
    _set_ssh_env(monkeypatch, tmp_path, KAJET_LOG_SSH_USER=None)
    with pytest.raises(LokiConfigError) as excinfo:
        SshTarget.from_env()
    message = str(excinfo.value)
    assert "KAJET_LOG_SSH_USER is unset" in message
    assert "KAJET_LOG_SSH_HOST" not in message
    assert "--source docker-logs" in message


def test_ssh_target_from_env_names_every_missing_variable_at_once(monkeypatch, tmp_path):
    """One failed run should report the whole gap, not the first hole in it."""
    _set_ssh_env(monkeypatch, tmp_path, KAJET_LOG_SSH_HOST=None, KAJET_LOG_SSH_USER=None)
    with pytest.raises(LokiConfigError) as excinfo:
        SshTarget.from_env()
    message = str(excinfo.value)
    assert "KAJET_LOG_SSH_HOST, KAJET_LOG_SSH_USER are unset" in message


def test_ssh_target_from_env_treats_blank_as_missing(monkeypatch, tmp_path):
    _set_ssh_env(monkeypatch, tmp_path, KAJET_LOG_SSH_KEY="   ")
    with pytest.raises(LokiConfigError, match="KAJET_LOG_SSH_KEY"):
        SshTarget.from_env()


def test_ssh_target_from_env_strips_surrounding_whitespace(monkeypatch, tmp_path):
    _set_ssh_env(monkeypatch, tmp_path, KAJET_LOG_SSH_HOST=" logs.example\n")
    assert SshTarget.from_env().host == "logs.example"


def test_ssh_target_falls_back_to_the_env_file(monkeypatch, tmp_path):
    env_file = _set_ssh_env(monkeypatch, tmp_path, KAJET_LOG_SSH_HOST=None)
    env_file.write_text("KAJET_LOG_SSH_HOST=from-file.example\n", encoding="utf-8")
    assert SshTarget.from_env().host == "from-file.example"


def test_ssh_target_process_env_wins_over_the_file(monkeypatch, tmp_path):
    """A prefixed variable is the one-off override; the file is the standing config."""
    env_file = _set_ssh_env(monkeypatch, tmp_path, KAJET_LOG_SSH_HOST="from-env.example")
    env_file.write_text("KAJET_LOG_SSH_HOST=from-file.example\n", encoding="utf-8")
    assert SshTarget.from_env().host == "from-env.example"


def test_ssh_target_env_file_syntax(monkeypatch, tmp_path):
    env_file = _set_ssh_env(
        monkeypatch,
        tmp_path,
        KAJET_LOG_SSH_HOST=None,
        KAJET_LOG_SSH_USER=None,
        KAJET_LOG_SSH_KEY=None,
    )
    env_file.write_text(
        "# connection for the log tunnel\n"
        "\n"
        "export KAJET_LOG_SSH_HOST=logs.example\n"
        '  KAJET_LOG_SSH_USER = "reader"  \n'
        "KAJET_LOG_SSH_KEY='/keys/reader'\n"
        "this line has no equals sign and is ignored\n",
        encoding="utf-8",
    )
    assert SshTarget.from_env() == SshTarget(host="logs.example", user="reader", key="/keys/reader")


def test_ssh_target_missing_message_names_the_env_file(monkeypatch, tmp_path):
    env_file = _set_ssh_env(monkeypatch, tmp_path, KAJET_LOG_SSH_KEY=None)
    with pytest.raises(LokiConfigError) as excinfo:
        SshTarget.from_env()
    assert str(env_file) in str(excinfo.value)


def test_ssh_target_env_file_absent_is_not_an_error(monkeypatch, tmp_path):
    """The file is optional — a fully exported environment needs none."""
    env_file = _set_ssh_env(monkeypatch, tmp_path)
    assert not env_file.exists()
    assert SshTarget.from_env().user == "reader"

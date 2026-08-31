from pathlib import Path

import pytest

from kajet_turbo.workspace import NoteFrontmatter, read_note_file, write_note_file


@pytest.fixture
def workspace(git_workspace_factory):
    return git_workspace_factory("workspace")


def test_frontmatter_canonicalizes_yaml_date(workspace):
    path = Path(workspace, "dated.md")
    path.write_text(
        "---\n"
        "id: dated1\n"
        "title: Dated\n"
        "tags: []\n"
        "created_at: 2026-01-01T00:00:00+00:00\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "occurred_at: 2026-03-22\n"
        "---\n"
        "Body\n"
    )

    meta, body = read_note_file(str(path))

    assert meta.occurred_at == "2026-03-22"
    assert meta.period is None
    write_note_file(str(path), meta, body)
    assert read_note_file(str(path))[0].occurred_at == "2026-03-22"


@pytest.mark.parametrize("period", ["2026-03-22", "2026-W12", "2026-03", "2026"])
def test_frontmatter_accepts_all_canonical_period_kinds(workspace, period):
    path = Path(workspace, "period.md")
    meta = NoteFrontmatter("period1", "Period", [], None, None, period=period)
    write_note_file(str(path), meta, "Body")
    assert read_note_file(str(path))[0].period == period


def test_frontmatter_rejects_two_temporal_facts():
    with pytest.raises(ValueError, match="mutually exclusive"):
        NoteFrontmatter(
            "id", "Title", [], None, None, occurred_at="2026-03-22", period="2026-W12"
        )

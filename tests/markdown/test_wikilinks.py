from kajet_turbo.markdown import (
    BrokenWikilinkError,
    IndexedNote,
    extract_wikilinks,
    render_markdown,
    rewrite_wikilinks,
    split_target,
)


def test_extract_simple():
    assert extract_wikilinks("link to [[Title]] here") == [("Title", None)]


def test_extract_with_folder_and_alias():
    assert extract_wikilinks("see [[Projekty/Plan|the plan]]") == [("Projekty/Plan", "the plan")]


def test_extract_multiple_on_one_line():
    assert extract_wikilinks("[[A]] and [[B/C|c]]") == [("A", None), ("B/C", "c")]


def test_extract_strips_inner_whitespace():
    assert extract_wikilinks("[[  A/B  |  alias  ]]") == [("A/B", "alias")]


def test_extract_ignores_inline_code():
    assert extract_wikilinks("real [[A]] but `[[B]]` is code") == [("A", None)]


def test_extract_ignores_fenced_code():
    body = "before [[Real]]\n\n```\n[[NotALink]]\n```\n"
    assert extract_wikilinks(body) == [("Real", None)]


def test_extract_empty_target_not_a_link():
    assert extract_wikilinks("[[]] and [[  ]]") == []


def test_extract_no_nesting_or_multiline():
    assert extract_wikilinks("[[a[b]]") == []
    assert extract_wikilinks("[[a\nb]]") == []


def test_split_target_title_only():
    assert split_target("Title") == ("", "Title")


def test_split_target_with_folder():
    assert split_target("A/B/Title") == ("A/B", "Title")


def test_split_target_strips_slashes_and_space():
    assert split_target("  /A/Title/  ".strip()) == ("A", "Title")


def test_split_target_normalizes_folder():
    # Windows-forbidden chars in folder get sanitized by normalize_folder.
    folder, title = split_target("A:B/Title")
    assert folder == "A B"
    assert title == "Title"


def test_split_target_relative_dotdot_does_not_raise():
    # ../ wikilinks are invalid references; they must not propagate ValueError as 500.
    folder, title = split_target("../SomeFolder/some-note")
    assert title == "some-note"
    # folder contains ".." — can't match any stored note, treated as broken
    assert ".." in folder


def test_render_resolved_is_clickable_anchor():
    html = render_markdown(
        "go to [[A/Plan|Plan]]",
        resolver=lambda target: IndexedNote("abc1234", "A", "Plan") if target == "A/Plan" else None,
        slug="myws",
    )
    assert '<a class="wikilink" href="/workspace/myws/notes/A/abc1234">Plan</a>' in html


def test_render_short_link_hrefs_resolved_folder_not_link_text():
    # [[Plan]] carries no folder; the href must point at where the note actually lives.
    html = render_markdown(
        "[[Plan]]",
        resolver=lambda target: IndexedNote("abc1234", "Deep/Er", "Plan"),
        slug="myws",
    )
    assert 'href="/workspace/myws/notes/Deep/Er/abc1234"' in html


def test_render_unresolved_is_broken_span():
    html = render_markdown("[[Ghost]]", resolver=lambda target: None, slug="myws")
    assert '<span class="wikilink-broken">Ghost</span>' in html


def test_render_without_resolver_is_broken_span():
    html = render_markdown("[[X|label]]")
    assert '<span class="wikilink-broken">label</span>' in html


def test_render_escapes_label():
    html = render_markdown("[[X|<script>]]", resolver=lambda target: None, slug="w")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_wikilink_in_code_is_literal():
    html = render_markdown("`[[X]]`", resolver=lambda target: IndexedNote("id", "", "X"), slug="w")
    assert "wikilink" not in html
    assert "[[X]]" in html


def test_render_keeps_gfm_table_and_strikethrough():
    html = render_markdown("~~old~~\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert "<table>" in html
    # strikethrough markup is consumed (no literal tildes), matching prior behaviour
    assert "~~" not in html


def test_broken_wikilink_error_message_lists_targets():
    err = BrokenWikilinkError(["A/Plan", "Ghost"])
    assert err.broken == ["A/Plan", "Ghost"]
    assert "[[A/Plan]]" in str(err)
    assert "[[Ghost]]" in str(err)
    assert isinstance(err, ValueError)


def _old_to_new(target: str) -> str | None:
    return "New/T" if target == "Old/T" else None


def test_rewrite_simple():
    body, changed = rewrite_wikilinks("see [[Old/T]]", _old_to_new)
    assert changed
    assert body == "see [[New/T]]"


def test_rewrite_preserves_alias():
    body, changed = rewrite_wikilinks("[[Old/T|label]]", _old_to_new)
    assert changed
    assert body == "[[New/T|label]]"


def test_rewrite_no_match_unchanged():
    body, changed = rewrite_wikilinks("[[Other]]", _old_to_new)
    assert not changed
    assert body == "[[Other]]"


def test_rewrite_same_target_counts_as_unchanged():
    body, changed = rewrite_wikilinks("[[ Old/T ]]", lambda target: target)
    assert not changed
    assert body == "[[ Old/T ]]"


def test_rewrite_callback_gets_stripped_target():
    seen: list[str] = []

    def spy(target: str) -> None:
        seen.append(target)
        return None

    rewrite_wikilinks("[[  A/B | alias ]] and [[]]", spy)
    assert seen == ["A/B"]


def test_render_xws_resolved_is_clickable_anchor():
    def xws_resolver(nid):
        return ("Target Note", "/workspace/other/notes/abc") if nid == "abc" else None

    html = render_markdown("see [[note:abc]]", xws_resolver=xws_resolver)
    assert (
        '<a class="wikilink xws-wikilink" href="/workspace/other/notes/abc">Target Note</a>' in html
    )


def test_render_xws_with_alias_uses_alias():
    def xws_resolver(nid):
        return ("Real Title", "/workspace/other/notes/abc") if nid == "abc" else None

    html = render_markdown("[[note:abc|Custom]]", xws_resolver=xws_resolver)
    assert ">Custom<" in html
    assert "xws-wikilink" in html


def test_render_xws_broken_shows_note_id():
    html = render_markdown("[[note:deadbeef]]", xws_resolver=lambda nid: None)
    assert '<span class="wikilink-broken">deadbeef</span>' in html


def test_render_xws_broken_with_alias_shows_alias():
    html = render_markdown("[[note:deadbeef|My Note]]", xws_resolver=lambda nid: None)
    assert '<span class="wikilink-broken">My Note</span>' in html


def test_render_xws_no_resolver_is_broken():
    html = render_markdown("[[note:abc]]")
    assert '<span class="wikilink-broken">abc</span>' in html


def test_extract_includes_xws_target():
    # extract_wikilinks returns raw target including "note:" prefix
    assert extract_wikilinks("[[note:abc123]]") == [("note:abc123", None)]


def test_render_xws_escapes_label():
    html = render_markdown("[[note:x]]", xws_resolver=lambda nid: ("<script>", "/w/n/x"))
    assert "<script>" not in html

"""Obsidian-style target resolution: suffix matching and deterministic ambiguity ranking."""

from kajet_turbo.markdown import (
    CaseCorrectedLink,
    IndexedNote,
    LinkIndex,
    join_target,
    resolve_content_links,
    split_target,
)


def _note(path: str) -> IndexedNote:
    """``"Folder/Sub/Title"`` -> IndexedNote whose note_id is the path itself."""
    return IndexedNote(path, *split_target(path))


def _index(*paths: str) -> LinkIndex:
    return LinkIndex(_note(p) for p in paths)


def _hit(index: LinkIndex, target: str, source_folder: str = "") -> str | None:
    note = index.resolve(target, source_folder)
    return note.note_id if note else None


def _detailed(index: LinkIndex, target: str, *, allow_casefold: bool = True):
    match = index.resolve_detailed(target, allow_casefold=allow_casefold)
    assert match is not None
    return match


# --- matching ---


def test_bare_title_matches_note_in_any_folder():
    assert _hit(_index("A/B/Title"), "Title") == "A/B/Title"


def test_bare_title_matches_root_note():
    assert _hit(_index("Title"), "Title") == "Title"


def test_full_path_matches_exactly():
    assert _hit(_index("A/B/Title"), "A/B/Title") == "A/B/Title"


def test_path_suffix_matches_at_segment_boundary():
    assert _hit(_index("A/B/Title"), "B/Title") == "A/B/Title"


def test_path_suffix_must_align_to_segment():
    # "B" is not a suffix of folder "AB" at a boundary.
    assert _hit(_index("AB/Title"), "B/Title") is None


def test_title_mismatch_is_none():
    assert _hit(_index("A/Title"), "Other") is None


def test_folder_part_not_in_path_is_none():
    assert _hit(_index("A/Title"), "X/Title") is None


def test_target_whitespace_and_slashes_are_tolerated():
    assert _hit(_index("A/Title"), " /A/Title/ ") == "A/Title"


def test_relative_dotdot_never_resolves():
    assert _hit(_index("A/Title"), "../A/Title") is None


def test_empty_index_resolves_nothing():
    assert _hit(LinkIndex([]), "Title") is None


# --- ranking ---


def test_exact_full_path_beats_nearer_suffix_match():
    # Source sits next to X/A/T, but [[A/T]] names root-level A/T explicitly.
    index = _index("A/T", "X/A/T")
    assert _hit(index, "A/T", source_folder="X/A") == "A/T"


def test_bare_title_prefers_root_note_over_nearer_one():
    # Pre-suffix behaviour preserved: [[T]] used to mean root-level T and still does.
    index = _index("T", "X/T")
    assert _hit(index, "T", source_folder="X") == "T"


def test_ambiguous_bare_title_prefers_same_folder_as_source():
    index = _index("A/T", "B/T")
    assert _hit(index, "T", source_folder="B") == "B/T"
    assert _hit(index, "T", source_folder="A") == "A/T"


def test_ambiguous_bare_title_prefers_deepest_shared_ancestor():
    index = _index("P/Q/T", "P/R/S/T", "Z/T")
    # Source in P/R: shares 2 segments with P/R/S, 1 with P/Q, 0 with Z.
    assert _hit(index, "T", source_folder="P/R") == "P/R/S/T"
    # Source in P: tie at 1 shared segment -> shallower folder wins.
    assert _hit(index, "T", source_folder="P") == "P/Q/T"


def test_ambiguous_without_source_context_is_shallowest_then_lexicographic():
    index = _index("B/T", "A/T", "A/X/T")
    assert _hit(index, "T") == "A/T"
    assert _hit(_index("B/T", "A/T"), "T") == "A/T"


def test_resolution_is_independent_of_insertion_order():
    forward = _index("A/T", "B/T", "C/T")
    backward = _index("C/T", "B/T", "A/T")
    for source in ("", "A", "B", "C", "Z"):
        assert _hit(forward, "T", source) == _hit(backward, "T", source)


# --- casefold fallback ---


def test_casefold_fallback_matches_title_only():
    index = _index("Plan projektu")
    assert _hit(index, "plan projektu") == "Plan projektu"
    assert _hit(index, "Plan projektu") == "Plan projektu"


def test_exact_match_wins_over_casefold_when_both_exist():
    index = _index("Readme", "readme")
    match = _detailed(index, "readme")
    assert match.chosen.note_id == "readme"
    assert match.casefold is False


def test_casefold_fallback_matches_folder_suffix():
    assert _hit(_index("Sub/Title"), "sub/Title") == "Sub/Title"


def test_casefold_fallback_matches_folder_and_title():
    assert _hit(_index("Sub/Title"), "sub/title") == "Sub/Title"


def test_casefold_fallback_polish_letter():
    index = _index("Łąka")
    assert _hit(index, "łąka") == "Łąka"
    assert _hit(index, "ŁĄKA") == "Łąka"


def test_casefold_fallback_german_sharp_s():
    # .lower() would NOT catch this: "Straße".lower() == "straße", not "strasse".
    assert _hit(_index("Straße"), "STRASSE") == "Straße"


def test_casefold_fallback_flags_the_match():
    index = _index("Readme")
    assert _detailed(index, "Readme").casefold is False
    assert _detailed(index, "readme").casefold is True


def test_casefold_fallback_disabled_by_allow_casefold():
    index = _index("Readme")
    assert index.resolve_detailed("readme", allow_casefold=False) is None
    assert index.resolve("readme", allow_casefold=False) is None


def test_casefold_case_twins_are_real_ambiguity_not_case_corrected():
    index = _index("A/Readme", "A/readme")
    res = resolve_content_links(index, "[[README]]", source_folder="")
    assert len(res.ambiguous) == 1
    assert res.ambiguous[0].target == "README"
    assert res.case_corrected == []


def test_casefold_tie_break_is_deterministic_regardless_of_insertion_order():
    forward = _index("A/Readme", "A/readme")
    backward = _index("A/readme", "A/Readme")
    assert _hit(forward, "README") == _hit(backward, "README")


# --- shortest_target ---


def test_shortest_target_is_bare_title_when_unique():
    index = _index("A/B/T")
    assert index.shortest_target(_note("A/B/T")) == "T"


def test_shortest_target_grows_until_unambiguous():
    index = _index("A/B/T", "C/B/T", "T")
    # Bare "T" hits root T (exact rule); "B/T" is ambiguous and the lexical tie-break
    # gives it to A/B/T, so C/B/T needs its full path.
    assert index.shortest_target(_note("A/B/T")) == "B/T"
    assert index.shortest_target(_note("C/B/T")) == "C/B/T"


def test_shortest_target_uses_source_proximity():
    index = _index("A/T", "B/T")
    assert index.shortest_target(_note("B/T"), source_folder="B") == "T"
    assert index.shortest_target(_note("B/T"), source_folder="A") == "B/T"


def test_shortest_target_respects_min_segments():
    index = _index("A/B/T")
    assert index.shortest_target(_note("A/B/T"), min_segments=2) == "B/T"
    assert index.shortest_target(_note("A/B/T"), min_segments=99) == "A/B/T"


def test_shortest_target_is_unaffected_by_a_casefold_twin():
    # Exact-title dict keys keep "Readme" and "readme" fully separate, so neither
    # note's shortest target is disturbed by the other's presence.
    index = _index("A/Readme", "A/readme")
    assert index.shortest_target(_note("A/Readme")) == "Readme"
    assert index.shortest_target(_note("A/readme")) == "readme"


# --- resolve_content_links ---


def test_resolve_content_links_splits_resolved_broken_and_xws():
    index = _index("A/Target")
    res = resolve_content_links(
        index, "[[Target]] [[Nope]] [[note:xyz]] [[Also/Missing]] [[Nope]]", source_folder=""
    )
    assert res.resolved_ids == {"A/Target"}
    assert res.broken == ["Also/Missing", "Nope"]
    assert res.xws_ids == ["xyz"]


def test_resolve_content_links_broken_pairs_are_storage_keys():
    res = resolve_content_links(LinkIndex([]), "[[Sub/Other]] and [[Ghost]]", source_folder="")
    assert res.broken_pairs == [("", "Ghost"), ("Sub", "Other")]


def test_resolve_content_links_ignores_code():
    res = resolve_content_links(LinkIndex([]), "`[[Ghost]]`", source_folder="")
    assert res.broken == []


def test_resolve_content_links_empty_body():
    res = resolve_content_links(_index("A/T"), "", source_folder="")
    assert res.resolved_ids == set() and res.broken == [] and res.xws_ids == []


def test_resolve_content_links_case_corrected_bucket():
    index = _index("A/Plan projektu")
    res = resolve_content_links(index, "[[plan projektu]]", source_folder="")
    assert res.resolved_ids == {"A/Plan projektu"}
    assert res.ambiguous == []
    assert res.case_corrected == [CaseCorrectedLink("plan projektu", _note("A/Plan projektu"))]


def test_join_target_roundtrip():
    assert join_target("", "T") == "T"
    assert join_target("A/B", "T") == "A/B/T"

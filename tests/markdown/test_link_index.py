"""Obsidian-style target resolution: suffix matching and deterministic ambiguity ranking."""

from kajet_turbo.markdown import IndexedNote, LinkIndex, join_target, resolve_content_links


def _index(*paths: str) -> LinkIndex:
    """Build an index from ``"Folder/Sub/Title"`` strings; note_id is the path itself."""
    notes = []
    for path in paths:
        folder, _, title = path.rpartition("/")
        notes.append(IndexedNote(path, folder, title))
    return LinkIndex(notes)


def _hit(index: LinkIndex, target: str, source_folder: str = "") -> str | None:
    note = index.resolve(target, source_folder)
    return note.note_id if note else None


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


def test_join_target_roundtrip():
    assert join_target("", "T") == "T"
    assert join_target("A/B", "T") == "A/B/T"

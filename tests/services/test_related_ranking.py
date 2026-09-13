from kajet_turbo.repositories.notes.types import RelatedEvidence
from kajet_turbo.services.notes.related_ranking import rank_related


def _ev(source_chunk_id, target_note_id, chunk_id, distance):
    return RelatedEvidence(
        source_chunk_id=source_chunk_id,
        target_note_id=target_note_id,
        target_chunk_id=chunk_id,
        distance=distance,
    )


def test_rank_related_empty_evidence_returns_empty():
    assert rank_related([], n_sources=0) == []
    assert rank_related([], n_sources=3) == []


def test_rank_related_prefers_closer_target():
    evidence = [
        _ev("s1", "near", "c-near", 0.1),
        _ev("s1", "far", "c-far", 0.9),
    ]
    ranked = rank_related(evidence, n_sources=1)
    assert [r.note_id for r in ranked] == ["near", "far"]
    assert ranked[0].best_distance == 0.1
    assert ranked[0].best_chunk_id == "c-near"
    assert ranked[0].source_chunk_id == "s1"


def test_rank_related_coverage_boosts_wider_evidence():
    # "a" and "b" have identical best margin against their own source's mean (each
    # source's head is exactly at its own mean distance, margin 0), but "a" is reached by
    # both sources and "b" only by one — coverage must break the tie in a's favour.
    evidence = [
        _ev("s1", "a", "c1", 0.2),
        _ev("s1", "filler1", "cf1", 0.2),
        _ev("s2", "a", "c3", 0.2),
        _ev("s2", "b", "c4", 0.2),
    ]
    ranked = rank_related(evidence, n_sources=2)
    by_id = {r.note_id: r for r in ranked}
    assert by_id["a"].hub_margin == by_id["b"].hub_margin == 0.0
    assert by_id["a"].coverage > by_id["b"].coverage
    assert ranked[0].note_id == "a"


def test_rank_related_tail_always_ranks_after_head():
    # One source with 12 distinct targets: the 10 nearest form the head (mean r_s over
    # them), the 2 farthest are tail. A tail target's raw margin can numerically exceed a
    # head target's score, but must still sort after every head note.
    targets = [(f"t{i}", float(i)) for i in range(12)]  # t0 closest ... t11 farthest
    evidence = [_ev("s1", note_id, f"c-{note_id}", dist) for note_id, dist in targets]

    ranked = rank_related(evidence, n_sources=1)

    head_ids = {r.note_id for r in ranked if r.is_head}
    tail_ids = {r.note_id for r in ranked if not r.is_head}
    assert head_ids == {f"t{i}" for i in range(10)}
    assert tail_ids == {"t10", "t11"}
    first_tail_index = next(i for i, r in enumerate(ranked) if not r.is_head)
    assert all(r.is_head for r in ranked[:first_tail_index])
    assert all(not r.is_head for r in ranked[first_tail_index:])


def test_rank_related_deterministic_tie_break_by_note_id():
    # Two sources, each contributing one target at the exact same distance -> identical
    # margin and coverage; note_id ascending must decide the order.
    evidence = [
        _ev("s1", "zzz", "c1", 0.5),
        _ev("s1", "aaa", "c2", 0.5),
    ]
    ranked = rank_related(evidence, n_sources=1)
    assert [r.note_id for r in ranked] == ["aaa", "zzz"]


def test_rank_related_best_evidence_wins_over_worse_from_another_source():
    # Target "t" is reached by two sources at different distances — the ranked item must
    # carry the WINNING (smaller-distance) source/chunk pair, not an arbitrary one.
    evidence = [
        _ev("s1", "t", "worse", 0.8),
        _ev("s1", "filler1", "f1", 0.1),
        _ev("s2", "t", "better", 0.1),
        _ev("s2", "filler2", "f2", 0.1),
    ]
    ranked = rank_related(evidence, n_sources=2)
    t = next(r for r in ranked if r.note_id == "t")
    assert t.best_chunk_id == "better"
    assert t.source_chunk_id == "s2"
    assert t.best_distance == 0.1

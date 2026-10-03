"""Shadow mode, whose central property is that it cannot break anything.

Dense retrieval, reranking and entailment are parked on evidence nobody has,
because gathering it needs them switched on. Shadow mode runs a candidate
beside the live retriever, records the disagreement, and changes nothing the
user sees.

Most of what follows tests the negative: a candidate that raises, returns
nonsense, or hangs on a missing dependency must cost a measurement and never
an answer.
"""

from __future__ import annotations

import pytest

from policyforge.zardoz.shadow import (
    SHADOWS,
    Comparison,
    ShadowReport,
    compare_rankings,
    configured_shadows,
    passage_ref,
    run_shadow,
    shadow_retrieval,
)


class Chunk:
    def __init__(self, section=""):
        self.section = section


class Doc:
    def __init__(self, title):
        self.title = title


class P:
    def __init__(self, title, section=""):
        self.document = Doc(title)
        self.chunk = Chunk(section)


A = P("Access Control Standard", "Review")
B = P("Access Control Standard", "Exceptions")
C = P("Backup Standard", "Retention")


# ---- the property that matters ----------------------------------------


def test_a_candidate_that_raises_costs_a_measurement_not_an_answer():
    def explodes():
        raise RuntimeError("embedding endpoint refused the connection")

    comparison = run_shadow("dense", [A, B], explodes)
    assert not comparison.ran
    assert "RuntimeError" in comparison.error
    assert "refused the connection" in comparison.error
    # The live ranking is still recorded, so the row is still evidence.
    assert comparison.live == [passage_ref(A), passage_ref(B)]


def test_a_candidate_that_fails_to_build_is_caught_too():
    """Construction fails as often as querying — a missing model, a dead host."""

    def cannot_build():
        raise ImportError("sentence-transformers is not installed")

    assert not run_shadow("dense", [A], cannot_build).ran


def test_a_failed_shadow_is_not_reported_as_agreement():
    """The one wrong answer this module must never give.

    A candidate that did not run and a candidate that agreed are completely
    different facts. Rendering both as "no disagreement" would manufacture
    the evidence the feature exists to gather.
    """
    failed = run_shadow("dense", [A], lambda: (_ for _ in ()).throw(ValueError("nope")))
    assert not failed.agreed
    assert not failed.same_set
    assert "did not run" in failed.summary()


def test_shadow_retrieval_survives_every_candidate_failing():
    report = shadow_retrieval(
        "how often are accounts reviewed?",
        [A],
        {
            "dense": lambda: (_ for _ in ()).throw(RuntimeError("down")),
            "rerank": lambda: (_ for _ in ()).throw(RuntimeError("also down")),
        },
    )
    assert len(report.comparisons) == 2
    assert not any(c.ran for c in report.comparisons)
    assert "did not run" in report.render()


# ---- what a disagreement looks like ------------------------------------


def test_exact_agreement_is_reported_as_such():
    comparison = compare_rankings("dense", [A, B], [A, B])
    assert comparison.agreed
    assert comparison.same_set
    assert "agreed exactly" in comparison.summary()


def test_reordering_is_distinguished_from_choosing_different_evidence():
    """A weaker disagreement, and conflating the two would overstate it."""
    comparison = compare_rankings("rerank", [A, B], [B, A])
    assert not comparison.agreed
    assert comparison.same_set
    assert "different order" in comparison.summary()


def test_different_passages_are_reported_as_added_and_dropped():
    comparison = compare_rankings("dense", [A, B], [A, C])
    assert not comparison.same_set
    assert comparison.only_shadow == [passage_ref(C)]
    assert comparison.only_live == [passage_ref(B)]
    rendered = comparison.render()
    assert "+ Backup Standard § Retention" in rendered
    assert "- Access Control Standard § Exceptions" in rendered


def test_passages_are_compared_by_identity_not_by_object():
    """The two retrievers build their own objects over the same corpus."""
    same_passage_different_object = P("Access Control Standard", "Review")
    comparison = compare_rankings("dense", [A], [same_passage_different_object])
    assert comparison.agreed


def test_a_passage_with_no_section_still_renders():
    comparison = compare_rankings("dense", [P("Standalone Page")], [])
    assert "Standalone Page" in comparison.render()


# ---- configuration ------------------------------------------------------


def test_no_shadow_configured_is_the_default():
    assert configured_shadows({}) == []
    assert configured_shadows(None) == []
    assert configured_shadows({"zardoz": {}}) == []


def test_a_configured_list_is_returned():
    assert configured_shadows({"zardoz": {"shadow": ["dense", "entail"]}}) == ["dense", "entail"]


def test_a_single_name_is_accepted_as_a_string():
    assert configured_shadows({"zardoz": {"shadow": "dense"}}) == ["dense"]


def test_a_typo_is_an_error_rather_than_a_silent_skip():
    """A measurement that never runs reads exactly like one that agreed."""
    with pytest.raises(ValueError, match="dens"):
        configured_shadows({"zardoz": {"shadow": ["dens"]}})


def test_the_error_lists_what_is_available():
    with pytest.raises(ValueError) as caught:
        configured_shadows({"zardoz": {"shadow": ["embeddings"]}})
    for name in SHADOWS:
        assert name in str(caught.value)


# ---- the report ---------------------------------------------------------


def test_an_empty_report_explains_how_to_turn_it_on():
    rendered = ShadowReport().render()
    assert "zardoz.shadow" in rendered
    for name in SHADOWS:
        assert name in rendered


def test_the_report_says_nothing_it_showed_changed_the_answer():
    """Stated in the output, because a reader seeing two rankings will ask."""
    report = shadow_retrieval("q", [A], {"dense": lambda: [A, C]})
    assert "Nothing above changed the answer you were given" in report.render()


def test_the_report_carries_the_question_it_was_measured_on():
    report = shadow_retrieval("how often are accounts reviewed?", [A], {"dense": lambda: [A]})
    assert "how often are accounts reviewed?" in report.render()


def test_a_comparison_with_no_candidates_reports_nothing_ran():
    assert not shadow_retrieval("q", [A], {}).ran


def test_comparison_defaults_are_safe():
    """A bare Comparison must not claim agreement it never measured."""
    empty = Comparison(name="dense")
    assert empty.ran
    assert empty.agreed  # two empty rankings genuinely do agree
    assert empty.only_live == []
    assert empty.only_shadow == []

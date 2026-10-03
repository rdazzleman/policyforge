"""Whether a cited passage actually supports the claim made from it.

This is the one place in the project where a model judges a model, and the
tests are written to keep that contained: the verdicts are opinions, they
never touch `check_answer`, and nothing here is an eval grader. What is
tested exactly is everything around the judgement — which sentence gets
judged, against which passage, and what happens when the judge is wrong or
unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from policyforge.entail import (
    CONTRADICTED,
    ENTAILED,
    NEUTRAL,
    Conflict,
    Unsupported,
    Verdict,
    conflicting_passages,
    get_entailer,
    unsupported_claims,
)
from policyforge.entail.base import cited_sentences


@dataclass
class FakeChunk:
    text: str


@dataclass
class FakePassage:
    chunk: FakeChunk


def _passages(*texts):
    return [FakePassage(FakeChunk(t)) for t in texts]


class FakeEntailer:
    """Returns a scripted verdict per (premise, hypothesis) pair."""

    def __init__(self, verdicts=None, default=ENTAILED):
        self.verdicts = verdicts or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def entails(self, premise, hypothesis):
        self.calls.append((premise, hypothesis))
        label = self.verdicts.get(premise, self.default)
        return Verdict(label=label, reason=f"scripted {label}")

    def check(self):
        return True


# --------------------------------------------------------------------------
# Which sentence is judged against which passage
# --------------------------------------------------------------------------


def test_a_trailing_citation_belongs_to_the_sentence_before_it():
    """The bug that made the first live run meaningless. The full stop lands
    before the marker, so a naive split hands the judge "[1]" and no claim —
    and it replies, reasonably, that no claim was supplied."""
    assert cited_sentences("The Officer approves certificates. [1]", 2) == [
        ("The Officer approves certificates.", [1])
    ]


def test_each_sentence_keeps_its_own_citations():
    assert cited_sentences("A is so. [1] B is so. [2][3]", 3) == [
        ("A is so.", [1]),
        ("B is so.", [2, 3]),
    ]


def test_an_uncited_sentence_is_not_judged():
    """`check_answer` already reports "makes claims without citing any
    passage" — a fact. Duplicating it with an opinion helps nobody."""
    assert cited_sentences("No citation here.", 2) == []


def test_markers_are_stripped_from_the_claim():
    """A judge shown "[2]" has been handed a token meaning nothing outside
    this tool, which can only distract from the sentence it must weigh."""
    assert cited_sentences("Inline [2] citation here.", 2) == [("Inline citation here.", [2])]


def test_a_citation_to_a_passage_that_does_not_exist_is_not_judged():
    """That is `check_answer`'s finding, and it is a fact."""
    assert cited_sentences("Out of range. [9]", 2) == []


# --------------------------------------------------------------------------
# The finding
# --------------------------------------------------------------------------


def test_a_supported_claim_produces_nothing():
    entailer = FakeEntailer(default=ENTAILED)

    assert (
        unsupported_claims("Records are retained. [1]", _passages("shall retain"), entailer) == []
    )


def test_an_unsupported_claim_is_reported_with_its_reason():
    """The measured case: the passage names IT Asset Management generating a
    certificate, the answer names the Security Officer approving one, and
    every deterministic check passes it."""
    entailer = FakeEntailer(default=NEUTRAL)

    found = unsupported_claims(
        "The Security Officer approves certificates. [1]",
        _passages("IT Asset Management shall generate certificates"),
        entailer,
    )

    assert len(found) == 1
    assert found[0].cited == [1]
    assert "scripted neutral" in str(found[0])


def test_one_supporting_passage_is_enough():
    """An answer may draw a claim from two documents and cite both, which is
    the behaviour the answering prompt asks for."""
    entailer = FakeEntailer({"first": NEUTRAL, "second": ENTAILED})

    found = unsupported_claims("A claim. [1][2]", _passages("first", "second"), entailer)

    assert found == []


def test_a_contradiction_outranks_a_neutral_in_the_report():
    """ "says otherwise" is a stronger finding than "says nothing about it",
    and burying it behind another passage's shrug would hide it."""
    entailer = FakeEntailer({"first": NEUTRAL, "second": CONTRADICTED})

    found = unsupported_claims("A claim. [1][2]", _passages("first", "second"), entailer)

    assert found[0].verdict.label == CONTRADICTED


def test_every_cited_passage_is_actually_consulted():
    entailer = FakeEntailer(default=NEUTRAL)

    unsupported_claims("A claim. [1][2]", _passages("first", "second"), entailer)

    assert [premise for premise, _ in entailer.calls] == ["first", "second"]


def test_an_empty_answer_is_not_a_finding():
    assert unsupported_claims("", _passages("a"), FakeEntailer()) == []


def test_the_finding_reads_as_a_sentence():
    finding = Unsupported("A claim.", [1, 2], Verdict(NEUTRAL, "the passage says otherwise"))

    assert "[1][2]" in str(finding)
    assert "the passage says otherwise" in str(finding)


# --------------------------------------------------------------------------
# When the cited passages disagree with each other
# --------------------------------------------------------------------------


def test_a_claim_one_passage_carries_and_another_denies_is_reported():
    """The silence this was built to end. `unsupported_claims` stops at the
    first passage that supports the sentence, so a sentence supported by [1]
    and contradicted by [2] produced no finding at all — and it produced
    that absence in the direction that reads as the documents agreeing."""
    entailer = FakeEntailer({"carries it": ENTAILED, "denies it": CONTRADICTED})

    found = conflicting_passages("A claim. [1][2]", _passages("carries it", "denies it"), entailer)

    assert len(found) == 1
    assert (found[0].supported_by, found[0].contradicted_by) == (1, 2)


def test_a_conflict_is_not_also_an_unsupported_claim():
    """The two findings are siblings, and a sentence belongs to one of them.
    Reporting a carried sentence as unsupported would say something false
    about which document said what."""
    entailer = FakeEntailer({"carries it": ENTAILED, "denies it": CONTRADICTED})

    assert (
        unsupported_claims("A claim. [1][2]", _passages("carries it", "denies it"), entailer) == []
    )


def test_passages_that_agree_are_not_a_conflict():
    """The half that gets skipped. Without it a finding that fires on every
    sentence passes every other test in this section."""
    for scripted in (
        {"first": ENTAILED, "second": ENTAILED},
        {"first": ENTAILED, "second": NEUTRAL},
        {"first": NEUTRAL, "second": NEUTRAL},
    ):
        found = conflicting_passages(
            "A claim. [1][2]", _passages("first", "second"), FakeEntailer(scripted)
        )
        assert found == [], scripted


def test_passages_that_all_deny_the_claim_are_not_a_conflict_either():
    """Nothing carries it, so the documents are not disagreeing with each
    other — they agree, about an answer neither of them supports. That is
    `unsupported_claims`' finding and it already makes it."""
    entailer = FakeEntailer(default=CONTRADICTED)

    assert conflicting_passages("A claim. [1][2]", _passages("first", "second"), entailer) == []


def test_which_passage_is_cited_first_does_not_change_the_finding():
    """Order-independence, pinned so a later optimisation cannot reintroduce
    the short-circuit quietly: judging only until something supports the
    sentence would find this conflict when the contradicting passage happens
    to be cited second and miss it when it is cited first. A conflict rate
    measured on that tracks how the answering model orders its citations."""
    scripted = {"carries it": ENTAILED, "denies it": CONTRADICTED}

    def named(order):
        passages = _passages(*order)
        found = conflicting_passages("A claim. [1][2]", passages, FakeEntailer(scripted))
        assert len(found) == 1
        return (
            passages[found[0].supported_by - 1].chunk.text,
            passages[found[0].contradicted_by - 1].chunk.text,
            found[0].sentence,
        )

    assert named(("carries it", "denies it")) == named(("denies it", "carries it"))


def test_the_conflict_reads_as_a_sentence_naming_both_passages():
    """ "they disagree" without saying which two sends the reader to read
    every cited passage to find out."""
    conflict = Conflict("A claim.", 1, 3, Verdict(CONTRADICTED, "the standard says annually"))

    assert "[1]" in str(conflict)
    assert "[3]" in str(conflict)
    assert "the standard says annually" in str(conflict)


# --------------------------------------------------------------------------
# The LLM judge
# --------------------------------------------------------------------------


class FakeProvider:
    def __init__(self, text='{"label": "entailed", "reason": "because"}', can_schema=True):
        self.text = text
        self.can_schema = can_schema
        self.calls: list[dict] = []

    def supports_schema(self):
        return self.can_schema

    def generate_json(self, **kwargs):
        from policyforge.llm.base import LLMResponse

        self.calls.append(kwargs)
        return LLMResponse(text=self.text, model="fake")


def _entailer(provider, **kw):
    from policyforge.entail.llm_entailer import LLMEntailer

    return LLMEntailer(provider=provider, **kw)


def test_the_verdict_is_constrained_to_three_labels():
    """A schema is what makes this tolerable rather than merely plausible:
    "Entailed.", "I'd say entailed" and a paragraph of hedging all become
    impossible."""
    provider = FakeProvider()

    _entailer(provider).entails("premise", "hypothesis")

    enum = provider.calls[0]["schema"]["json_schema"]["schema"]["properties"]["label"]["enum"]
    assert set(enum) == {ENTAILED, NEUTRAL, CONTRADICTED}


def test_the_passage_and_the_claim_both_reach_the_judge():
    provider = FakeProvider()

    _entailer(provider).entails("the passage text", "the claim text")

    assert "the passage text" in provider.calls[0]["prompt"]
    assert "the claim text" in provider.calls[0]["prompt"]


def test_the_reason_survives_onto_the_verdict():
    """A bare label is not actionable; naming what the passage says is."""
    provider = FakeProvider('{"label": "neutral", "reason": "names a different team"}')

    verdict = _entailer(provider).entails("p", "h")

    assert verdict.label == NEUTRAL
    assert verdict.reason == "names a different team"
    assert not verdict.supports


def test_a_model_that_cannot_be_held_to_a_schema_is_refused():
    """An unconstrained verdict is not worth having."""
    provider = FakeProvider(can_schema=False)

    with pytest.raises(RuntimeError, match="schema"):
        _entailer(provider).entails("p", "h")


def test_naming_no_model_is_refused():
    """Configuring this at all is a decision to use a *different* model than
    the one writing the answers."""
    from policyforge.entail.llm_entailer import LLMEntailer

    with pytest.raises(ValueError, match="other"):
        LLMEntailer(model=None)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_entailment_is_off_unless_asked_for():
    assert get_entailer({"llm": {"provider": "anthropic"}}) is None
    assert get_entailer({"entail": {}}) is None


def test_an_unknown_provider_names_what_is_supported():
    with pytest.raises(ValueError, match="llm"):
        get_entailer({"entail": {"provider": "nonsense"}})

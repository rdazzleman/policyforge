"""NIST's own "must", quoted from an action the sentence cites, is not an obligation (#320).

#309's check refuses any binding word in a sentence citing only the Playbook.
Playbook Govern 1.7 Action 4 itself says "artifacts that must be preserved",
so a sentence faithfully restating the action it cites was a false ERROR. It
was the one of b5's 17 stored glm sentences still flagged after #319.

80's ruling: a binding word is exempt only inside a run of at least
`QUOTE_MIN_WORDS` (6) consecutive words that occurs verbatim, case-insensitive
and whitespace-normalised, in the text of a Playbook action the SAME sentence
cites, looked up in the shipped catalog. A paraphrase is still flagged.

The action text used here is the catalog's own, read in the test, so a
change to the catalog moves the cases rather than silently emptying them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.content.deontic import QUOTE_MIN_WORDS, playbook_obligations

CATALOG = Path(__file__).resolve().parent.parent / "data" / "frameworks" / "nist-ai-rmf-playbook"
CITES_1_7_4 = "[NIST AI RMF Playbook Govern 1.7 Action 4]"
CITES_1_4_1 = "[NIST AI RMF Playbook Govern 1.4 Action 1]"


def _action(action_id: str) -> str:
    rows = json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))
    return next(
        e["description"]
        for r in rows
        for e in r["enhancements"]
        if e["enhancement_id"] == action_id
    )


def _flagged(sentence: str, tag: str) -> bool:
    return len(playbook_obligations(f"{sentence} {tag}\n")) == 1


def test_the_cases_below_are_built_from_the_catalogs_own_wording():
    """The premise, measured: Action 4 carries the phrase the cases quote."""
    assert "artifacts that must be preserved for fulsome understanding" in _action(
        "Govern 1.7 Action 4"
    )
    assert QUOTE_MIN_WORDS == 6


def test_a_six_word_quote_from_the_cited_action_passes():
    # "artifacts that must be preserved for" -- six words, verbatim.
    assert not _flagged(
        "NIST suggests listing artifacts that must be preserved for audits.", CITES_1_7_4
    )


def test_a_five_word_quote_fails():
    # "that must be preserved for" -- five: "records" is not "artifacts".
    assert _flagged("NIST suggests listing records that must be preserved for audits.", CITES_1_7_4)


def test_the_same_quote_fails_when_the_action_is_not_cited():
    """Verbatim from the Playbook is not enough; from a CITED action is."""
    sentence = "NIST suggests listing artifacts that must be preserved for audits."
    assert _flagged(sentence, CITES_1_4_1)


def test_a_paraphrased_must_fails():
    assert _flagged(
        "NIST suggests policies for artifacts that must be kept to understand the system.",
        CITES_1_7_4,
    )


def test_a_six_word_run_in_no_cited_action_fails():
    """1d's case: six words of the right length, carrying "must", but not
    NIST's -- so the match is against the action text, not a word count."""
    assert _flagged("NIST suggests that all model records must be kept on file.", CITES_1_7_4)


def test_a_must_outside_the_quoted_run_still_binds():
    """The exemption covers the quoted words only, not the sentence."""
    assert _flagged(
        "NIST suggests policies for artifacts that must be preserved for fulsome "
        "understanding, and the organization must log each one.",
        CITES_1_7_4,
    )


def test_a_quote_starting_at_must_with_a_supplied_subject_fails():
    """1d's smuggle, ruled (b) by 80 on #325. Eight of Action 4's words,
    verbatim, but the run starts AT "must", so the subject "the
    organization" is the writer's, not NIST's."""
    sentence = (
        "NIST suggests that the organization must be preserved for fulsome "
        "understanding or execution."
    )
    assert "must be preserved for fulsome understanding or execution" in _action(
        "Govern 1.7 Action 4"
    )
    assert _flagged(sentence, CITES_1_7_4)


def test_a_run_beginning_exactly_at_must_fails():
    # "must be preserved for fulsome understanding or" -- seven words, first is
    # "must". ("understanding." with its full stop would not match the
    # catalog's "understanding", and a five-word run fails for length, not
    # for this: the first version of this test did exactly that.)
    sentence = "NIST suggests records must be preserved for fulsome understanding or audit."
    assert "must be preserved for fulsome understanding or" in _action("Govern 1.7 Action 4")
    assert _flagged(sentence, CITES_1_7_4)


def test_one_quoted_word_before_must_is_enough():
    """The boundary on the other side: "that" precedes "must" inside the
    run, so the modal sits inside NIST's clause. That is all it shows: see
    the stated limit below, where the antecedent of "that" is the writer's."""
    assert not _flagged(
        "NIST suggests keeping records that must be preserved for fulsome understanding.",
        CITES_1_7_4,
    )


@pytest.mark.parametrize(
    "sentence",
    [
        # 9b's and 1d's measurements on #325: the run begins with NIST's
        # relative pronoun, so it qualifies, but its antecedent is the writer's.
        "NIST suggests keeping the organization's own records that must be preserved "
        "for fulsome understanding or execution.",
        "NIST suggests that the organization that must be preserved for fulsome "
        "understanding or execution.",
    ],
)
def test_the_stated_limit_a_relative_pronoun_passes(sentence):
    """**A limit, pinned so it is seen rather than discovered** (80's ruling:
    stated beside QUOTE_MIN_WORDS, not closed by a second word count, which
    would only move the smuggle one word right). If this starts failing, the
    rule was tightened and the comment beside the bound must change too."""
    assert not _flagged(sentence, CITES_1_7_4)


def test_the_exposure_is_the_three_playbook_actions_that_carry_a_binding_word():
    """What the limit above can reach: only an action whose own text binds.
    Pinned by id, not by count, so a catalog change names what it added (80
    on #325). Today one "must" and one "may not". There were two "may not":
    #364 reads Measure 3.2 Action 1's "may not be measurable" as the
    possibility it is. Measure 2.9 Action 6's "explanations may not
    accurately summarize" is a thing's capability, #364's named miss, so it
    stays. A new one fails here and the limit is revisited."""
    from policyforge.content.deontic import BINDING, classify

    rows = json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))
    binding = {
        e["enhancement_id"]: classify(e["description"])
        for r in rows
        for e in r["enhancements"]
        if classify(e["description"]) in BINDING
    }
    assert sum(len(r["enhancements"]) for r in rows) == 459, "the population moved"
    assert binding == {
        "Govern 1.7 Action 4": "obligation",
        "Measure 2.9 Action 6": "prohibition",
    }


def test_the_quote_is_case_and_whitespace_insensitive():
    assert not _flagged(
        "NIST suggests listing ARTIFACTS  that\tmust be Preserved FOR audits.", CITES_1_7_4
    )


def test_a_merged_tag_naming_the_action_counts_as_citing_it():
    tag = "[NIST AI RMF Playbook Govern 1.7 Action 1 | NIST AI RMF Playbook Govern 1.7 Action 4]"
    assert not _flagged("NIST suggests listing artifacts that must be preserved for audits.", tag)


@pytest.mark.parametrize(
    "sentence",
    [
        # The exemption is only for the binding check; the subject rule is unchanged.
        "The organization lists artifacts that must be preserved for fulsome understanding.",
        "NIST requires artifacts that must be preserved for fulsome understanding.",
    ],
)
def test_quoting_nist_does_not_excuse_the_wrong_subject(sentence):
    assert _flagged(sentence, CITES_1_7_4)

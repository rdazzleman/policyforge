"""A Playbook sentence may open with one of four closed shapes before "NIST suggests" (#319).

`framed_as_nists` read the subject from the first word, so "Among the 7
actions NIST suggests for Govern 1.4, NIST suggests ..." -- a compliant
attribution -- was an ERROR in `policyforge check`. b5 found it in #301's
production run: glm-5.3-flash via OpenRouter wrote 17 such sentences, one per
subcategory, and the gate flagged 17 of 17.

80's corrected ruling: the opener is one of a CLOSED list of shapes, with the
subcategory id checked against the shipped catalog. The first ruling allowed
any prepositional phrase with no binding verb, and ba measured three
commitments passing inside it ("... the organization will adopt these, NIST
suggests ..."), because `classify` does not count will / commits / is
responsible as binding. So anything not in the list fails, by default.

**The stored sentences:** `fixtures/playbook_glm_among_sentences.md` holds
the 17 lines verbatim from b5's run301 output,
`glm-ai-governance-accountability.md` (topic "AI Governance & Accountability",
$0.0036). They are replayed here with no model call.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from policyforge.content.deontic import analyze, classify, playbook_obligations

FIXTURE = Path(__file__).parent / "fixtures" / "playbook_glm_among_sentences.md"
CATALOG = Path(__file__).resolve().parent.parent / "data" / "frameworks" / "nist-ai-rmf"
TAG = "[NIST AI RMF Playbook Govern 1.4 Action 1]"


def _flagged(sentence: str) -> bool:
    return len(playbook_obligations(f"{sentence} {TAG}\n")) == 1


# -- b5's stored run ------------------------------------------------------------------


def _stored() -> list:
    text = FIXTURE.read_text(encoding="utf-8")
    stored = [s for s in analyze(text) if s.cites_only_the_playbook]
    assert len(stored) == 17, f"the fixture should hold b5's 17 sentences, found {len(stored)}"
    return stored


def _subcategory(sentence: str) -> str:
    return re.match(r"Among the \d+ actions NIST suggests for (Govern \d\.\d),", sentence).group(1)


def test_the_stored_run_opens_every_sentence_with_among():
    """The population the fix is for: all 17 use the one opener b5 measured."""
    assert all(s.text.startswith("Among the ") for s in _stored())


def test_all_seventeen_stored_sentences_pass():
    """17 of 17. On #319 alone it was 16: Govern 1.7 restates Playbook
    Govern 1.7 Action 4's own words, "artifacts that must be preserved",
    which #309's whole-sentence check refused. #320 exempts a binding word
    quoted verbatim from an action the sentence cites, which this is."""
    assert playbook_obligations(FIXTURE.read_text(encoding="utf-8")) == []

    (govern_1_7,) = [s for s in _stored() if _subcategory(s.text) == "Govern 1.7"]
    assert "artifacts that must be preserved" in govern_1_7.text
    assert classify(govern_1_7.text) == "obligation"
    assert "NIST AI RMF Playbook Govern 1.7 Action 4" in " ".join(govern_1_7.citations)


# -- the four shapes pass ----------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "For Govern 1.4, NIST suggests reviewing the inventory.",
        "Among the 7 actions NIST suggests for Govern 1.4, NIST suggests reviewing the inventory.",
        "Among the 7 actions the Playbook lists for Govern 1.4, NIST suggests reviewing it.",
        "Across these actions, the Playbook lists documentation practices.",
        "Across the 7 actions, NIST suggests reviewing the inventory.",
        "Of the 7 actions for Govern 1.4, NIST suggests reviewing the inventory.",
        # Case and whitespace are free, and list markers and emphasis are stripped first.
        "among  the 7 actions nist suggests for GOVERN 1.4 ,  NIST suggests reviewing it.",
        "- **For Govern 1.4,** NIST recommends reviewing the inventory.",
    ],
)
def test_each_listed_opener_passes(sentence):
    assert not _flagged(sentence)


# -- everything else fails -------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        # ba's three: commitments inside a free-text phrase, which the first ruling passed.
        "For Govern 1.4 the organization will adopt these, NIST suggests reviewing the inventory.",
        "Under this Standard Acme commits to the inventory, NIST suggests reviewing it.",
        "For Govern 1.4 the organization is responsible for this, NIST suggests reviewing it.",
        # The same inside the other listed prepositions: a shape is not a phrase.
        "Among our commitments the organization will adopt these, NIST suggests reviewing it.",
        "Across the organization we will adopt these, NIST suggests reviewing the inventory.",
        "Of the actions Acme commits to, NIST suggests reviewing the inventory.",
        # 80's named must-fails on the first ruling.
        "Acme will keep an inventory, NIST suggests reviewing it.",
        "For Govern 1.4, the organization will maintain an inventory.",
        # Binding words, in the opener position and after a valid opener.
        "For compliance we must, NIST suggests reviewing the inventory.",
        "For Govern 1.4, NIST suggests that the organization shall maintain an inventory.",
        "For Govern 1.4, NIST requires an inventory.",
    ],
)
def test_commitments_and_obligations_fail(sentence):
    assert _flagged(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        # Two stacked openers: one only, by the bound.
        "For Govern 1.4, across these actions, NIST suggests reviewing the inventory.",
        # A comma inside the opener ends it early; what follows is not the subject.
        "Among the 7 actions, which NIST suggests for Govern 1.4, NIST suggests reviewing it.",
        # No comma at all: the opener never ends.
        "Among the 7 actions NIST suggests for Govern 1.4 NIST suggests reviewing it.",
        # The id slot cannot carry text: not a Core subcategory, or not an id.
        "For Govern 9.9, NIST suggests reviewing the inventory.",
        "For Acme 1.4, NIST suggests reviewing the inventory.",
        "Of the 7 actions for Map 1.9, NIST suggests reviewing the inventory.",
        # A lookalike prefix: "For" must be the whole word.
        "Formal Govern 1.4, NIST suggests reviewing the inventory.",
        # N is digits.
        "Across the seven actions, NIST suggests reviewing the inventory.",
    ],
)
def test_shapes_outside_the_bound_fail(sentence):
    assert _flagged(sentence)


def test_a_harmless_opener_not_in_the_list_is_a_documented_false_alarm():
    """80's ruling: shown, not hidden. It fails loudly and a reword fixes it."""
    assert _flagged("In practice, NIST suggests reviewing the inventory.")
    assert not _flagged("NIST suggests, in practice, reviewing the inventory.")


# -- the id check reads the real catalog ------------------------------------------------


def test_the_subcategory_set_is_the_catalogs_pinned_shape():
    """Two instruments: the ids `deontic` reads, against the shape pin a
    person wrote in `framework.yaml` (#189)."""
    from policyforge.content.deontic import _core_subcategories
    from policyforge.ingest.ai_rmf import expected_shape

    ids = _core_subcategories()
    assert len(ids) == expected_shape(CATALOG / "framework.yaml")[1]
    assert {"govern 1.4", "manage 4.3", "map 1.1", "measure 2.13"} <= ids

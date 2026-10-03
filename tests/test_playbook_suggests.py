"""A sentence citing the NIST AI RMF Playbook may say NIST suggests; never shall (#300).

The Playbook is voluntary (NIST: "Playbook suggestions are voluntary"). A
generated sentence that turns a suggestion into "must", and cites the
Playbook for it, asserts something NIST did not say. 80's ruling (A): a
Playbook-cited sentence never binds, whatever its subject; an obligation the
organization adopts goes in its own sentence, without the Playbook tag.

Measured ($0, local `qwen3:14b-pf`, one Standard per run): before the prompt
rule, 10 of 10 Playbook-cited sentences read "The organization must ...".
With it, five runs flagged 0/9, 9/10, 7/9, 0/9 and 9/14 -- the rule reduces
the failure and does not remove it, and the check caught every instance.
A first report of "9 of 9 NIST suggests" was one run, stated as a result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.content.check import ERROR, check_tree
from policyforge.content.deontic import (
    analyze,
    playbook_obligations,
    playbook_tagged_headings,
    weakened_citations,
)
from policyforge.content.grounding import unanchored

TAG = "[NIST AI RMF Playbook Govern 1.1 Action 1]"


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------


def test_nist_suggests_passes():
    """The passing case, and the form the prompt now asks for."""
    text = f"NIST suggests maintaining awareness of applicable legal requirements. {TAG}\n"

    assert playbook_obligations(text) == []


def test_an_obligation_citing_the_playbook_is_refused():
    text = f"The organization **must** maintain awareness of legal requirements. {TAG}\n"

    (found,) = playbook_obligations(text)
    assert "maintain awareness" in found.text


def test_the_organization_as_subject_is_still_refused():
    """Ruling (A): "whatever its subject". This presents NIST's suggestion
    as the organization's requirement just as "NIST requires" would."""
    text = f"Acme Health shall maintain an inventory of AI systems. {TAG}\n"

    assert len(playbook_obligations(text)) == 1


def test_a_citation_on_the_next_line_is_credited_to_the_sentence():
    """Generated Standards put the tag under the sentence; `deontic` credits
    it backwards, and the check must see it there."""
    text = f"The organization must maintain an AI inventory.\n{TAG}\n"

    assert len(playbook_obligations(text)) == 1


@pytest.mark.parametrize(
    "sentence",
    [
        # 1d's five forms on #309, each passing the first version's verb list.
        "NIST requires the organization to maintain an inventory.",
        "The organization will maintain an inventory.",
        "The organization is responsible for maintaining an inventory.",
        "Maintain an inventory of AI systems.",
        "The organization ensures an inventory is maintained.",
        # Forms nobody listed, to show the default: the allow-list fails them.
        "Acme Health commits to keeping an inventory of AI systems.",
        "It is mandatory to maintain an inventory of AI systems.",
        "NIST mandates an inventory of AI systems.",
        # NIST as subject, but binding anyway: a Playbook sentence never binds.
        "NIST suggests that the organization must maintain an inventory.",
    ],
)
def test_anything_not_framed_as_nists_is_refused(sentence):
    """**Default-deny** (80's ruling on #309). A verb list chased paraphrase;
    the rule now allows NIST or the Playbook as the subject, and fails
    everything else, including forms nobody thought of."""
    assert len(playbook_obligations(f"{sentence} {TAG}\n")) == 1


@pytest.mark.parametrize(
    "sentence",
    [
        "The Playbook groups these actions under Govern 1.",
        "The Playbook lists three actions for AI inventories.",
        "NIST also recommends reviewing the inventory.",
        "1. NIST proposes documenting each system's owner.",
        "- **NIST** recommends an inventory of AI systems.",
    ],
)
def test_a_descriptive_sentence_framed_as_nists_passes(sentence):
    """**The passing twins**, because an allow-list fails by default and a
    false alarm on a sentence that asserts nothing would get it switched off
    (1d's caution, 80's refinement)."""
    assert playbook_obligations(f"{sentence} {TAG}\n") == []


def test_a_sentence_about_nist_with_another_subject_is_refused():
    """A stated consequence of the ruling: the SUBJECT decides. "One action
    NIST lists is ..." asserts nothing, but its subject is "One action", so
    it fails. Proposed as a passing twin on #309; 80's refinement puts the
    allow condition on the subject, and this is where that line falls."""
    assert len(playbook_obligations(f"One action NIST lists is an inventory. {TAG}\n")) == 1


def test_other_frameworks_are_not_this_checks_business():
    text = "The organization must enforce approved authorizations. [NIST 800-53 AC-3]\n"

    assert playbook_obligations(text) == []


def test_citations_are_recorded_per_sentence():
    """The field the check reads, which `deontic` did not record before."""
    (statement,) = analyze(f"NIST suggests an inventory. {TAG}\n")

    assert statement.citations == (TAG,)
    assert statement.cites_the_playbook


# --------------------------------------------------------------------------
# The requirement-strength exemption, and its limit
# --------------------------------------------------------------------------


def test_the_correct_form_is_not_reported_as_a_weakened_citation():
    """Before #300, "NIST suggests ... [Playbook]" was cited and did not bind,
    so it warned as weakened, while "must ... [Playbook]" passed."""
    assert weakened_citations(f"NIST suggests an AI inventory. {TAG}\n") == []


def test_a_non_binding_sentence_citing_a_binding_source_still_warns():
    """The exemption is only for sentences whose EVERY citation is a
    Playbook tag (80). A binding source stays held to binding strength."""
    text = "Teams should consider enforcing authorizations. [NIST 800-53 AC-3]\n"

    assert len(weakened_citations(text)) == 1


# --------------------------------------------------------------------------
# A merged tag: a binding source beside the Playbook (80's second ruling)
# --------------------------------------------------------------------------

MIXED = "[NIST 800-53 CM-8 | NIST AI RMF Playbook Govern 1.6 Action 1]"


def test_a_mixed_tag_that_binds_is_not_an_error():
    """The binding source carries the obligation, so binding is correct."""
    text = f"The organization must maintain an inventory of system components. {MIXED}\n"

    assert playbook_obligations(text) == []
    assert weakened_citations(text) == []


def test_a_mixed_tag_is_mixed_whichever_reference_comes_first():
    """Split on `|` before deciding: with the Playbook reference first, the
    tag as a whole begins like a Playbook tag."""
    playbook_first = "[NIST AI RMF Playbook Govern 1.6 Action 1 | NIST 800-53 CM-8]"
    text = f"The organization must maintain an inventory of components. {playbook_first}\n"

    assert playbook_obligations(text) == []


def test_a_mixed_tag_that_does_not_bind_is_held_to_the_binding_source():
    """The exemption is for sentences citing ONLY the Playbook. Beside a
    binding source, a non-binding sentence still weakens that source."""
    text = f"NIST suggests maintaining an inventory of system components. {MIXED}\n"

    assert playbook_obligations(text) == []
    assert len(weakened_citations(text)) == 1


def test_only_playbook_tags_split_across_a_merged_tag_are_still_only_playbook():
    """Two Playbook references in one merged tag are still Playbook-only."""
    both = "[NIST AI RMF Playbook Govern 1.6 Action 1 | NIST AI RMF Playbook Govern 1.6 Action 2]"

    assert len(playbook_obligations(f"The organization must keep an inventory. {both}\n")) == 1


# --------------------------------------------------------------------------
# The adopted obligation, in its own sentence
# --------------------------------------------------------------------------


#: The section #300 produces most: a suggestion, then the organization's own
#: uncited obligation -- and NO binding source citing beside them. The first
#: version of this test also had a CM-8 "must" in the section, and that line
#: alone made the section count as citing (1d's finding on #309).
ADOPTED = (
    "# AI Governance Standard\n\n"
    "## Inventory\n\n"
    f"NIST suggests maintaining an inventory of AI systems. {TAG}\n\n"
    "Every AI system must be recorded in the inventory.\n"
)


def test_an_adoption_beside_a_playbook_suggestion_is_reported():
    """**80's ruling (2) on #309.** A Playbook citation makes its section
    count as citing, so the organization's uncited "must" beside it is
    reported -- the organization's decision, for a person to confirm."""
    assert playbook_obligations(ADOPTED) == []

    (finding,) = unanchored(ADOPTED)
    assert "recorded in the inventory" in finding.claim.text


def test_the_same_section_without_the_playbook_tag_behaves_as_before():
    """The twin: with no citation at all, the section cites nothing and the
    "must" is document boilerplate, exactly as before this change."""
    assert unanchored(ADOPTED.replace(f" {TAG}", "")) == []


def test_a_non_binding_citation_to_another_source_does_not_make_a_section_citing():
    """**Narrow, not general** (80): only the Playbook gets this effect. A
    non-binding sentence citing 800-53 leaves the rule as it was."""
    text = ADOPTED.replace(
        f"NIST suggests maintaining an inventory of AI systems. {TAG}",
        "An inventory of system components is useful. [NIST 800-53 CM-8]",
    )

    assert unanchored(text) == []


def test_an_adoption_phrased_as_requires_is_reported_by_neither():
    """**A stated limit.** "Acme Health requires ..." is not binding to
    `deontic` (must, shall, is required to), so `_check_unanchored` does not
    see it -- and it carries no Playbook tag, so the framing rule does not
    apply. An adopted obligation phrased that way is checked by nothing."""
    text = ADOPTED.replace(
        "Every AI system must be recorded", "Acme Health requires every AI system to be recorded"
    )

    assert playbook_obligations(text) == []
    assert unanchored(text) == []


# --------------------------------------------------------------------------
# Headings may not cite the Playbook (80's ruling on #309)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    [
        # 9b's two probes: each passed, because `analyze` blanks headings.
        f"### NIST requires an AI inventory {TAG}",
        f"### The organization must keep an AI inventory {TAG}",
        # What a generated Procedure actually wrote, 9 times of 9.
        f"### 1. Maintain Awareness of Legal Requirements {TAG}",
    ],
)
def test_a_heading_citing_the_playbook_is_refused(heading):
    """A heading tag scopes every step beneath it, so it makes a suggestion
    an instruction by position, whatever the heading says."""
    (found,) = playbook_tagged_headings(f"{heading}\n\n1. Keep the inventory.\n")
    assert found[0] == 1


def test_a_mixed_heading_is_refused_too():
    """Even merged with a binding source: the ruling makes no exception."""
    mixed = "[NIST 800-53 CM-8 | NIST AI RMF Playbook Govern 1.6 Action 1]"

    assert len(playbook_tagged_headings(f"### Inventory {mixed}\n")) == 1


def test_a_heading_citing_only_a_binding_source_passes():
    """The twin: headings may still carry an ordinary framework tag."""
    assert playbook_tagged_headings("### Inventory [NIST 800-53 CM-8]\n\n1. Keep it.\n") == []


def test_a_playbook_tag_in_the_body_is_not_a_heading():
    assert playbook_tagged_headings(f"## Inventory\n\nNIST suggests an inventory. {TAG}\n") == []


# --------------------------------------------------------------------------
# Through the content check
# --------------------------------------------------------------------------


def _tree(tmp_path: Path, body: str) -> Path:
    doc = tmp_path / "standards" / "ai-governance.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "---\ntitle: AI Governance Standard\ntier: standard\nowner: AI Team\n---\n\n" + body,
        encoding="utf-8",
    )
    return tmp_path


def test_the_content_check_reports_it_as_an_error(tmp_path):
    report = check_tree(
        _tree(tmp_path, f"## A\n\nThe organization must keep an inventory. {TAG}\n")
    )

    playbook = [f for f in report.findings if "NIST AI RMF Playbook" in f.message]
    assert len(playbook) == 1
    assert playbook[0].severity == ERROR


def test_the_content_check_reports_a_tagged_heading_as_an_error(tmp_path):
    report = check_tree(_tree(tmp_path, f"### Keep an inventory {TAG}\n\n1. Keep it.\n"))

    headings = [
        f for f in report.findings if "a heading cites the NIST AI RMF Playbook" in f.message
    ]
    assert len(headings) == 1
    assert headings[0].severity == ERROR


def test_the_content_check_passes_the_correct_form(tmp_path):
    report = check_tree(_tree(tmp_path, f"## A\n\nNIST suggests keeping an inventory. {TAG}\n"))

    assert not [f for f in report.findings if "NIST AI RMF Playbook" in f.message]
    assert not [f for f in report.findings if "does not bind" in f.message or "weaken" in f.message]

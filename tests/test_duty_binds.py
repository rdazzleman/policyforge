"""An actor "is responsible|accountable for" doing something binds (#363).

80's ruling on 5b's measurement: this is the one modal-free family that binds.
It is guarded against NIST's suggestion frame (#177: the Playbook suggests,
never requires), against a document or framework subject, and against a noun
where the gerund should be. Imperatives, "will", "is to", "is expected to"
and "is subject to" do not bind, by ruling; the last test pins that, so a
later "fix" has to change a test that names the ruling.

The binding and NIST cases are verbatim from 5b's 83-Standard corpus. The
subject cases are written from the language, not from the rule's word lists
(the lesson of #273 and #326): gerund nouns in both places they can stand.
"""

from __future__ import annotations

import pytest

from policyforge.content import deontic
from policyforge.content.deontic import BINDING, NONE, OBLIGATION, classify

#: Seven sentences from generated Standards, each an actor given a duty.
CORPUS_BINDS = [
    "Network Security is responsible for granting this authorization.",
    "Third-Party Risk is responsible for approving and managing these agreements.",
    "Infrastructure Security is responsible for defining and maintaining this coverage definition.",
    "AI Risk / Product is accountable for verifying that the documentation required "
    "by this standard exists and is current for every AI system in scope.",
    "- Site Reliability Engineering is accountable for maintaining these procedures "
    "and for their execution during emergency operations.",
    "Data Governance is responsible for keeping these artifacts current as part of "
    "the annual review cycle defined in Section 4.",
    "Facilities / Physical Security is responsible for retaining these artifacts and "
    "making them available to auditors on request.",
]

#: The corpus's three NIST-suggests hits: the duty sits in a relative clause
#: inside NIST's own sentence.
CORPUS_NIST = [
    "NIST suggests, among its 4 actions for Govern 1.6, establishing policies that "
    "define the creation and maintenance of AI system inventories (Action 1); "
    "establishing policies that define a specific individual or team that is "
    "responsible for maintaining the inventory (Action 2)",
    "NIST suggests, among its 4 actions for Govern 1.6, establishing policies that "
    "define the creation and maintenance of AI system inventories [NIST AI RMF "
    "Playbook Govern 1.6 Action 1], establishing policies that define a specific "
    "individual or team that is responsible for maintaining the inventory.",
    "NIST suggests establishing policies that define a specific individual or team "
    "that is responsible for maintaining the inventory.",
]


@pytest.mark.parametrize("sentence", CORPUS_BINDS)
def test_an_actor_made_responsible_for_doing_something_binds(sentence):
    assert classify(sentence) == OBLIGATION


@pytest.mark.parametrize("sentence", CORPUS_NIST)
def test_nists_suggestions_do_not_bind(sentence):
    assert classify(sentence) not in BINDING


def test_nist_as_subject_never_binds_through_this_rule():
    """Not only the relative clause: with NIST speaking, a named actor in a
    that-clause does not bind through this family either (#177)."""
    assert classify("NIST suggests that the AI Risk team is responsible for reviewing it.") == NONE
    # Its modal words are still read, as before #363.
    assert classify("NIST suggests that the organization must review it.") == OBLIGATION


@pytest.mark.parametrize(
    "sentence",
    [
        # 5b's C4, ruled `none` on #360: a noun, not a gerund.
        "The System Owner is responsible for any use of the account.",
        "Security Operations is responsible for the outcome of this process.",
        # -ing words that are not verbs.
        "The CISO is responsible for everything in this section.",
        "Network Security is responsible for nothing outside the perimeter.",
        "The on-call engineer is responsible for anything raised during the shift.",
    ],
)
def test_a_noun_after_for_does_not_bind(sentence):
    assert classify(sentence) == NONE


@pytest.mark.parametrize(
    "sentence",
    [
        "This Standard is responsible for defining the minimum controls.",
        "The Access Control Policy is responsible for setting the baseline.",
        "This process is accountable for producing the following artifacts.",
        "NIST is responsible for publishing the framework.",
        "It is responsible for ensuring coverage.",
        "The Playbook is responsible for suggesting actions.",
    ],
)
def test_a_document_framework_or_pronoun_subject_does_not_bind(sentence):
    assert classify(sentence) == NONE


def test_a_gerund_noun_subject_is_an_activity_not_an_actor():
    """1d's caution from #367, first direction: an -ing word as the whole
    subject names an activity."""
    assert classify("Testing is responsible for detecting regressions.") == NONE
    assert classify("Monitoring is accountable for alerting on drift.") == NONE


def test_the_it_department_is_an_actor_and_the_pronoun_is_not():
    """1d on #371: lowercasing made the department "IT" the pronoun "it".
    Judged by case, as a subject and as an elided subject's opening."""
    assert classify("IT is responsible for backing up all servers nightly.") == OBLIGATION
    assert classify("Corporate IT is accountable for patching the fleet.") == OBLIGATION
    assert (
        classify("IT performs the backups; and is accountable for verifying each restore.")
        == OBLIGATION
    )
    assert classify("It is responsible for backing up all servers nightly.") == NONE
    assert classify("Backups run nightly, and it is responsible for alerting.") == NONE
    assert (
        classify("It performs the backups; and is accountable for verifying each restore.") == NONE
    )


def test_a_department_spelled_like_a_gerund_is_an_actor():
    """The other direction: some departments are spelled like a gerund, and
    a head that is a noun but contains one is still an actor."""
    assert classify("Engineering is responsible for granting access.") == OBLIGATION
    assert classify("Accounting is accountable for reconciling the ledger.") == OBLIGATION
    assert classify("The Testing team is responsible for running the scans.") == OBLIGATION
    assert classify("AI / ML Engineering is accountable for selecting metrics.") == OBLIGATION


def test_the_head_is_read_before_of():
    assert classify("The owner of this Standard is responsible for reviewing it.") == OBLIGATION


def test_the_duty_clause_after_a_joiner_is_judged_on_its_own_subject():
    """The subject is taken from the start of its clause, not the sentence."""
    assert (
        classify(
            "Other teams participate in individual steps as described below, but "
            "AI Risk / GRC is responsible for defining the overall process."
        )
        == OBLIGATION
    )
    assert (
        classify(
            "Security Operations maintains the register; this Standard is responsible "
            "for defining its fields."
        )
        == NONE
    )
    # Judged from the sentence's start, the head would be a many-word subject;
    # from its clause, it is one gerund, an activity.
    assert (
        classify(
            "The register is kept by Security Operations; testing is responsible for "
            "detecting regressions."
        )
        == NONE
    )


def test_a_duty_in_a_relative_clause_does_not_bind():
    """Outside NIST's frame too: "an individual who is responsible for ..."
    describes whom a sentence names; it does not give that person a duty."""
    assert (
        classify("The inventory names the individual who is responsible for maintaining it.")
        == NONE
    )
    assert classify("Each system has an owner that is responsible for approving changes.") == NONE


def test_an_elided_subject_is_the_sentences():
    """From the corpus: "...; and is accountable for ensuring ..." has the
    sentence's subject, and binds when that is an actor, not a document."""
    assert (
        classify(
            "Security Program / GRC performs risk assessments; maintains the risk "
            "register; and is accountable for ensuring the process operates on cadence."
        )
        == OBLIGATION
    )
    assert (
        classify("This Standard applies to all systems and is responsible for ensuring coverage.")
        == NONE
    )


def test_it_outranks_a_weaker_modal():
    """Strongest wins, as for the modals: a permission beside a duty is a
    duty with latitude."""
    assert (
        classify(
            "Other teams may assist, but Network Security is responsible for approving "
            "each request."
        )
        == OBLIGATION
    )
    assert (
        classify(
            "Network Security must not approve its own requests and is responsible for "
            "logging them."
        )
        == deontic.PROHIBITION
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Review the risk assessment annually.",
        "Never share credentials over email.",
        "Do not permit ePHI flows to begin until the agreement is on file.",
        "IAM Engineering performs this procedure end to end.",
        "The organization will review the inventory annually.",
        "The functions are to be performed in a secure area.",
        "This process is expected to produce the following artifacts.",
        "All workforce members are subject to the ePHI access requirements of Section 8.",
    ],
)
def test_the_families_ruled_non_binding_stay_non_binding(sentence):
    """80's ruling on #363, with 5b's numbers in `classify`'s docstring.
    Changing this is changing a ruling, not fixing a gap."""
    assert classify(sentence) not in BINDING


def test_the_rule_is_not_a_heading_modal():
    """`_HEADING_MODALS` reads `_MODALITY_PATTERNS` as a list of modal phrases;
    the duty rule is kept out of it, so a title is not made a statement."""
    assert "responsible" not in deontic._HEADING_MODALS
    (heading,) = deontic.heading_statements(
        "## Network Security Is Responsible for Granting Access"
    )
    assert heading.modality == NONE

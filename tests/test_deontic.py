"""Deontic strength: how firmly a document states what it cites.

The interesting cases are the false positives. A checker that flags every
`should` in a corpus of policy prose produces a report nobody reads, and
this project's own integrity checks keep making the same argument — a check
that cries wolf teaches people to scroll past the one time it matters. So
most of what is tested here is what must *not* be reported.
"""

from __future__ import annotations

import pytest

from policyforge.content.deontic import (
    NONE,
    OBLIGATION,
    PERMISSION,
    PROHIBITION,
    RECOMMENDATION,
    analyze,
    binding_share,
    classify,
    weakened_citations,
)


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("The policy must be reviewed annually.", OBLIGATION),
        ("IT Asset Management shall retain documentation.", OBLIGATION),
        ("Teams are required to recertify accounts.", OBLIGATION),
        ("Media must not leave the facility.", PROHIBITION),
        ("Personnel shall not share credentials.", PROHIBITION),
        ("Teams should consider recertifying accounts.", RECOMMENDATION),
        ("Encryption is recommended for backups.", RECOMMENDATION),
        ("Media may be transported by approved courier.", PERMISSION),
        ("Retention beyond six years is optional.", PERMISSION),
        ("This Standard establishes requirements for media handling.", NONE),
    ],
)
def test_modality_is_classified(sentence, expected):
    assert classify(sentence) == expected


def test_a_prohibition_is_not_read_as_an_obligation():
    """ "must not" contains "must". Tested in the wrong order, every
    prohibition in the corpus becomes an obligation to do the forbidden
    thing."""
    assert classify("Media must not be released without authorisation.") == PROHIBITION


def test_the_strongest_modality_wins():
    """Requirement prose nests latitude inside obligation. Reading the
    first modal verb would report the firmest sentence as the weakest."""
    sentence = "Records must be retained in written form (which may be electronic)."

    assert classify(sentence) == OBLIGATION


def test_markdown_emphasis_does_not_hide_a_modal():
    """Generated Standards write "**must**"."""
    assert classify("The policy **must** be reviewed.") == OBLIGATION


def test_headings_are_not_statements():
    """ "### 4.1 Media Protection" commits nobody to anything, and a heading
    ending in a numbered section splits a sentence in half."""
    text = "## 4.1 Account Review\n\nAccounts must be recertified.\n"

    assert [s.text for s in analyze(text)] == ["Accounts must be recertified."]


def test_a_citation_on_its_own_line_belongs_to_the_sentence_above():
    """Generated Standards put it there: the requirement ends with a full
    stop and the citation sits underneath. A naive split hands it to
    whatever follows."""
    text = "Accounts must be recertified quarterly.\n[NIST AC-2 Low/Moderate/High]\n"

    statements = analyze(text)

    assert len(statements) == 1
    assert statements[0].cited
    assert statements[0].modality == OBLIGATION


def test_a_trailing_citation_is_not_absorbed_by_the_next_requirement():
    """The bug this cost an iteration to find: with no terminator after the
    bracket, the citation merges *forward* and inherits the next sentence's
    `must`, so a weakened requirement reports clean."""
    text = (
        "Teams should consider recertifying accounts.\n"
        "[NIST AC-2]\n\n"
        "Records must be retained for six years.\n"
        "[NIST MP-1]\n"
    )

    weakened = weakened_citations(text)

    assert [s.modality for s in weakened] == [RECOMMENDATION]
    assert "should consider" in weakened[0].text


def test_a_bound_citation_is_not_reported():
    text = "Records must be retained for six years.\n[NIST MP-1]\n"

    assert weakened_citations(text) == []


def test_an_uncited_recommendation_is_not_reported():
    """A `should` on its own is often right — a Policy states principles,
    and commentary advises. Only a sentence claiming a control is judged."""
    text = "Teams should consider reviewing this annually.\n"

    assert weakened_citations(text) == []


def test_a_cited_bare_fact_is_reported_as_its_own_kind_of_weakness():
    """ "records are reviewed" states a practice; an assessor asks where it
    says they must be. Distinguished from `should` so the report can say
    which defect it found."""
    text = "Sanitization records are reviewed periodically.\n[NIST MP-6]\n"

    weakened = weakened_citations(text)

    assert [s.modality for s in weakened] == [NONE]


def test_binding_share_ignores_plain_prose():
    """A Standard is mostly purpose, scope and definitions. Counting those
    as unbound would make every document look permissive."""
    text = (
        "This Standard establishes requirements for media handling.\n"
        "It applies to all personnel.\n"
        "Records must be retained for six years.\n"
        "Teams should review them.\n"
    )

    assert binding_share(text) == (1, 2)


def test_line_numbers_point_at_the_document_a_reader_has_open():
    text = "## Heading\n\nfiller sentence here.\n\nAccounts must be recertified.\n[NIST AC-2]\n"

    accounts = next(s for s in analyze(text) if "recertified" in s.text)

    assert accounts.line == 5


# --------------------------------------------------------------------------
# Wired into `policyforge check`
# --------------------------------------------------------------------------


def _tree(tmp_path, tier_dir, body):
    doc = tmp_path / tier_dir
    doc.mkdir(parents=True, exist_ok=True)
    (doc / "thing.md").write_text(f"# Thing\n\n{body}", encoding="utf-8")
    return tmp_path


def test_check_warns_about_a_weakened_requirement_in_a_standard(tmp_path):
    from policyforge.content.check import check_tree

    root = _tree(tmp_path, "standards", "Teams should consider recertifying.\n[NIST AC-2]\n")

    report = check_tree(root)

    assert [f for f in report.warnings if "cited requirement" in f.message]
    assert report.ok, "a judgement about wording must not block a publish"


def test_check_leaves_a_policy_alone(tmp_path):
    """A Policy states principles. "should" is allowed to mean should."""
    from policyforge.content.check import check_tree

    root = _tree(tmp_path, "policies", "Teams should consider recertifying.\n[NIST AC-2]\n")

    report = check_tree(root)

    assert not [f for f in report.warnings if "cited requirement" in f.message]


#: Every shipped catalog's citation shape, one per framework. Four of the
#: eight carry a dot in the identifier and every one of those was invisible
#: to this module until 2026-09-21.
CITATION_SHAPES = [
    "[NIST 800-53 AC-2]",
    "[FedRAMP AC-2(1)]",
    "[ARC-AMPE AC-2]",
    "[HIPAA Security Rule 164.308(a)(3)(i)]",
    "[NIST 800-171 03.01.01]",
    "[NIST AI RMF Govern 1.1]",
    "[Substance Use Disorder Records 2.16]",
    "[Information Blocking 171.203]",
]


@pytest.mark.parametrize("citation", CITATION_SHAPES)
def test_a_dotted_identifier_does_not_split_the_sentence(citation: str):
    """**The defect: `_SENTENCE_RE` split through the citation itself.**

    `[^.!?]+(?:[.!?]+|$)` breaks at every full stop, including the ones
    inside an identifier. `164.308(a)(3)(i)` became three fragments and
    `03.01.01` four — none of them a citation — so `cited` was False and
    `weakened_citations` reported nothing.

    The failure direction is **silence**: a document whose requirements
    were all correctly cited and all written as "should consider" read as
    clean. It worked for 800-53, FedRAMP and ARC-AMPE — and not for HIPAA,
    which is the framework this product exists to serve.

    Parametrised over every shipped shape rather than the one that was
    reported, because the reported instance is never the population.
    """
    statements = analyze(f"The organization should consider access control. {citation}")

    assert len(statements) == 1, f"the citation was split: {[s.text for s in statements]}"
    assert statements[0].cited


@pytest.mark.parametrize("citation", CITATION_SHAPES)
def test_a_weakened_requirement_is_reported_for_every_citation_shape(citation: str):
    """The check exists to catch a cited requirement rendered as advice.
    It had never fired for a dotted identifier."""
    assert weakened_citations(f"The organization should consider encryption. {citation}") != []


def test_an_uncited_obligation_is_still_a_statement():
    """**The rule that must NOT be ported from `entail`.**

    `entail/base.py:cited_sentences` deliberately skips uncited sentences,
    and its reasoning is sound there: `check_answer` already reports
    "makes claims without citing any passage" as a fact.

    That reasoning does not hold here. `content/check.py:_check_uncited`
    fires only when a document cites **nothing at all** — it short-circuits
    on `source_tags(doc.body)` being non-empty — so a document with one
    good tag and ten uncited obligations passes it completely. Inheriting
    the skip would silently drop the worst case on this side: an
    obligation with no anchor at all.

    Identified by policyforge-b5, who wrote the rule being ported and
    said which half of it not to bring.
    """
    text = (
        "The organization shall enforce access control. [NIST 800-53 AC-2]\n"
        "The organization shall review logs quarterly.\n"
    )
    statements = analyze(text)

    assert len(statements) == 2
    assert [s.cited for s in statements] == [True, False]
    assert all(s.modality == "obligation" for s in statements)


def test_an_abbreviation_does_not_end_a_sentence():
    """`45 C.F.R.` is how every HIPAA citation is written in prose, and
    `U.S. Department` appears in essentially every one of these documents.
    Both come from the boundary rule this imports rather than from
    anything added here."""
    statements = analyze(
        "A practice under 45 C.F.R. 171.203(a) qualifies. [Information Blocking 171.203]"
    )

    assert len(statements) == 1
    assert statements[0].cited

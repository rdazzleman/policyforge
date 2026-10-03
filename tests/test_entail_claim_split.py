"""A claim is judged whole, or the judge is answering about debris.

`cited_sentences` used to cut on every `.!?`, so `Section 4.2`, `164.308`,
`45 C.F.R.`, `Rev. 5`, `e.g.` and `99.9%` each ended a sentence. That cost
two things per occurrence and only one of them was visible. The judge was
handed a fragment — `2 of the Standard` — and reported it unsupported,
which appears in the finding list as a false positive. And the claim the
fragment was carved out of was never judged at all, which appears as
nothing. **A claim that is never examined looks exactly like a claim that
passed**, so the invisible half is the one that matters.

The fixtures below are not invented. `REAL_FRAGMENTS` are the claim strings
recorded by a real entailment run over the Zardoz eval suite, 26 of 42
findings with parseable text. Each case in `CASES` is an input whose output
*under the old rule* reproduces one of those recorded strings exactly, and
a test asserts that reproduction — so a fixture that drifts away from what
the tool actually produced fails rather than quietly becoming synthetic.

The other assertion is the one worth stating plainly: each case checks that
the **whole claim** comes back in some unit, not merely that no fragment
appears. Those are different claims and only the first catches a silent
drop.
"""

from __future__ import annotations

import re

import pytest

from policyforge.entail.base import cited_sentences

#: Verbatim from a measured run (policyforge-b5, 2026-09-18), with the
#: number of findings each string accounted for. Every one of these is the
#: judge being asked about debris.
REAL_FRAGMENTS = {
    "2 .": 9,
    "2, which is not included in the passages provided .": 4,
    '"': 3,
    '2" , and Section 4.': 3,
    "2 of the Standard .": 1,
    '2" .': 1,
    '2" , but Section 4.': 1,
    '2" of the Standard , but Section 4.': 1,
    "1 owned by IAM Engineering .": 1,
    "2, which the passage references but does not include .": 1,
    "1, owned by IAM Engineering, states that "
    '"Account entitlements are recertified quarterly by the system owner" .': 1,
}

#: The rule as it stood at f5f8988, frozen so the fixtures can be held to
#: what it really did. Must not be "tidied" into a call to the new code:
#: the moment it delegates, the faithfulness test proves nothing.
_OLD_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_CITATION_RE = re.compile(r"\[(\d+)\]")


def _old_units(text: str) -> list[str]:
    units = []
    for match in _OLD_SENTENCE_RE.finditer(text):
        segment = match.group(0).strip()
        if segment:
            units.append(" ".join(_CITATION_RE.sub("", segment).split()))
    return units


#: (what the answer said, the recorded fragment the old rule made of it,
#: the claim that was silently dropped). The dropped claim is what the
#: judge should have been asked about and never was.
CASES = [
    pytest.param(
        "The requirement appears in the Access Control Standard § 4.2 [1].",
        "2 .",
        "The requirement appears in the Access Control Standard",
        id="section-decimal",
    ),
    pytest.param(
        "Records are kept per Section 4.2 of the Standard [1].",
        "2 of the Standard .",
        "Records are kept per Section 4.2 of the Standard",
        id="section-decimal-midsentence",
    ),
    pytest.param(
        "Entitlements are recertified quarterly, per the Access Control Standard "
        "§ 4.2, which is not included in the passages provided [1].",
        "2, which is not included in the passages provided .",
        "Entitlements are recertified quarterly",
        id="tail-after-decimal",
    ),
    pytest.param(
        "The control is defined in § 4.2, which the passage references but does not include [1].",
        "2, which the passage references but does not include .",
        "The control is defined in",
        id="passage-reference-tail",
    ),
    pytest.param(
        'Passage 2.1, owned by IAM Engineering, states that "Account entitlements '
        'are recertified quarterly by the system owner" [1].',
        "1, owned by IAM Engineering, states that "
        '"Account entitlements are recertified quarterly by the system owner" .',
        "Passage 2.1, owned by IAM Engineering",
        id="numbered-passage-reference",
    ),
]


@pytest.mark.parametrize("text,recorded,dropped", CASES)
def test_the_fixture_reproduces_a_recorded_fragment(text, recorded, dropped):
    """The fixture is faithful to output the tool really produced."""
    assert recorded in REAL_FRAGMENTS, "fixture cites a fragment that was never recorded"
    assert recorded in _old_units(text), (
        "the old rule does not turn this input into the recorded fragment, "
        "so the fixture no longer represents the defect"
    )


@pytest.mark.parametrize("text,recorded,dropped", CASES)
def test_the_whole_claim_comes_back_in_one_unit(text, recorded, dropped):
    """Not "no fragment appears" — the claim itself must be judged.

    A rule that returned nothing at all would pass a no-fragment assertion
    and drop every claim, which is the failure this file exists to catch.
    """
    units = [sentence for sentence, _ in cited_sentences(text, 3)]

    assert units, "no claim reached the judge at all"
    assert any(dropped in unit for unit in units), (
        f"the claim {dropped!r} is in no returned unit: {units}"
    )
    assert recorded not in units


@pytest.mark.parametrize("text,recorded,dropped", CASES)
def test_the_citation_still_reaches_the_claim(text, recorded, dropped):
    """A whole claim with its citation lost would be judged against the
    wrong passage, or dropped by the caller for having none."""
    assert [cited for _, cited in cited_sentences(text, 3)] == [[1]]


# ---- the shapes the decimal rule alone would have left broken ------------


@pytest.mark.parametrize(
    "text,claim",
    [
        (
            "Per 45 C.F.R. 164.312 the rule applies [1].",
            "Per 45 C.F.R. 164.312 the rule applies",
        ),
        (
            "The rule at 164.308(a)(3)(ii)(B) requires termination procedures [1].",
            "The rule at 164.308(a)(3)(ii)(B) requires termination procedures",
        ),
        ("See NIST SP 800-53 Rev. 5 for detail [1].", "See NIST SP 800-53 Rev. 5 for detail"),
        (
            "Encrypt data at rest, e.g. database volumes [1].",
            "Encrypt data at rest, e.g. database volumes",
        ),
        (
            "Contact the CISO (i.e. the security lead) first [1].",
            "Contact the CISO (i.e. the security lead) first",
        ),
        ("Uptime is 99.9% and RTO is 4.5 hours [1].", "Uptime is 99.9% and RTO is 4.5 hours"),
        (
            "Approval is required (see Fig. 3) before release [1].",
            "Approval is required (see Fig. 3) before release",
        ),
    ],
)
def test_abbreviations_and_legal_references_stay_whole(text, claim):
    """`45 C.F.R. 164.312` is how every HIPAA citation is written, so a rule
    that special-cased digit-dot-digit would leave the framework this tool
    exists to handle broken while looking fixed."""
    units = [sentence for sentence, _ in cited_sentences(text, 3)]

    assert any(claim in unit for unit in units), f"{claim!r} is in no unit: {units}"


# ---- an initial followed by a capital is not a sentence end -------------


@pytest.mark.parametrize(
    "text,claim",
    [
        (
            "The U.S. Department of Health and Human Services enforces it [1].",
            "The U.S. Department of Health and Human Services enforces it",
        ),
        ("The D.C. office is exempt [1].", "The D.C. office is exempt"),
        ("Reviewed by J. Smith on Tuesday [1].", "Reviewed by J. Smith on Tuesday"),
        ("Notify H.H.S. within 60 days [1].", "Notify H.H.S. within 60 days"),
        ("The vendor (Acme Inc.) signs a BAA [1].", "The vendor (Acme Inc.) signs a BAA"),
    ],
)
def test_an_initial_before_a_capital_does_not_end_a_claim(text, claim):
    """`U.S. Department` is a sentence start by every other rule here, and
    that phrase is in essentially every HIPAA document. Found by
    policyforge-1d attacking the first version of this fix."""
    units = [sentence for sentence, _ in cited_sentences(text, 3)]

    assert any(claim in unit for unit in units), f"{claim!r} is in no unit: {units}"


@pytest.mark.parametrize(
    "text",
    [
        "Contact the CISO. The lead reviews it [1].",
        "Reviewed by IAM. The owner signs [1].",
    ],
)
def test_a_sentence_ending_in_an_acronym_still_ends(text):
    """The exception is for a *one-letter* token. `CISO` and `IAM` are
    words, so a claim after one of them is still judged on its own."""
    units = [sentence for sentence, _ in cited_sentences(text, 3)]

    assert len(units) == 1, units
    assert "CISO" not in units[0] and "IAM" not in units[0]


# ---- what must keep working ---------------------------------------------


def test_two_real_sentences_are_still_two_claims():
    """Under-splitting is the safe direction, not the goal. An answer that
    makes two claims must still be judged as two."""
    text = "Access is reviewed quarterly [1]. Keys rotate annually [2]."

    assert cited_sentences(text, 3) == [
        ("Access is reviewed quarterly .", [1]),
        ("Keys rotate annually .", [2]),
    ]


def test_a_trailing_citation_is_still_credited_to_the_claim_above_it():
    """The shape the original author handled and got right: the citation
    follows the full stop, so a naive split leaves the claim uncited and
    hands the judge "[1]" and nothing else."""
    text = "Media is destroyed. [1] Keys rotate annually. [2]"

    assert cited_sentences(text, 3) == [
        ("Media is destroyed.", [1]),
        ("Keys rotate annually.", [2]),
    ]


def test_an_uncited_sentence_is_not_judged():
    assert cited_sentences("Keys rotate annually. Access is reviewed [1].", 3) == [
        ("Access is reviewed .", [1])
    ]


def test_a_citation_out_of_range_is_dropped():
    assert cited_sentences("Access is reviewed [9].", 3) == []


def test_empty_text_is_no_claims():
    assert cited_sentences("", 3) == []
    assert cited_sentences(None, 3) == []

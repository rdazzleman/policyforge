"""A predicate that states a requirement or prohibition binds (#360).

`classify` read "MFA is required." as none, though "Users are required to use
MFA." bound, and "are not permitted" as none, though "are prohibited" bound.
80's ruling: such a predicate binds like its verb twin, when it states the
requirement ABOUT its subject; "No action is required.", "as required by
HIPAA" and "where required" do not.

**Measured over the 33 generated Standards and two saved rate drafts:** one
sentence changes, "...; MFA is required for all other non-privileged account
types." (none -> obligation), which removes a false "statement of fact"
warning; `check` goes 159 -> 158 findings with none added, and no heading's
reading changes. A draft of this fix read "When organizations are not
permitted to delete ..., Acme Health shall ..." as a prohibition; the
conditional rule now covers the new prohibitions too.
"""

from __future__ import annotations

import pytest

from policyforge.content.deontic import (
    _HEADING_MODAL_RE,
    _MODALITY_PATTERNS,
    NONE,
    OBLIGATION,
    PERMISSION,
    PROHIBITION,
    classify,
)


@pytest.mark.parametrize(
    ("sentence", "modality"),
    [
        # 80's must-bind
        ("MFA is required.", OBLIGATION),
        ("MFA is required for all remote access.", OBLIGATION),
        ("MFA is mandatory.", OBLIGATION),
        ("Encryption is required for all laptops.", OBLIGATION),
        ("Personal devices are not permitted.", PROHIBITION),
        ("Personal devices are not allowed.", PROHIBITION),
        # 80's must-stay
        ("No action is required.", NONE),
        ("Records are kept as required by HIPAA.", NONE),
        ("Encrypt data where required.", NONE),
        ("Approval may be required.", PERMISSION),
        # look-alikes written from the domain
        ("Document what is required by the policy.", NONE),
        ("Retain the evidence that is required.", NONE),
        ("If approval is required, request it in the ticket.", NONE),
        ("MFA is not required for guest Wi-Fi.", NONE),
        ("Nothing in this Standard is required of contractors.", NONE),
        # a condition does not outrank the sentence's own verb (the corpus case)
        ("When staff are not permitted to delete a record, Acme Health shall log it.", OBLIGATION),
        ("Where remote access is permitted, MFA is required.", OBLIGATION),
        # the forms around it
        ("- MFA is required for administrators.", OBLIGATION),
        ("**MFA** is **required**.", OBLIGATION),
        ("Annual training is mandatory for all staff.", OBLIGATION),
        ("MFA is required; guests are exempt.", OBLIGATION),
    ],
)
def test_classify(sentence, modality):
    assert classify(sentence) == modality


#: 5b's (policyforge-f8) verbatim sentences from public sources on #360, and 80's
#: scope ruling on them: O1-O5 and P8's form bind; L1-L4 and L13 must not
#: change. Real text, collected without reading `_MODALITY_PATTERNS`.
@pytest.mark.parametrize(
    ("row", "sentence", "modality"),
    [
        ("O1", "Compliance with a Policy is mandatory.", OBLIGATION),
        ("O2", "Multi-factor Authentication is required in the following situations:", OBLIGATION),
        ("O3", "The following procedures are required:", OBLIGATION),
        ("O4", "Conformance with this paragraph is required by January 1, 2026.", OBLIGATION),
        ("O5", "At that point, corrective action is required.", OBLIGATION),
        ("L1", "Implementation specifications are required or addressable.", NONE),
        (
            "L2",
            "System agencies or organizations except as required by State or Federal law.",
            NONE,
        ),
        ("L3", "When required by system changes;", NONE),
        (
            "L4",
            "However, exceptions may be required based on operational mission or need.",
            PERMISSION,
        ),
        (
            "L13",
            "Protect against any reasonably anticipated uses or disclosures of such information "
            "that are not permitted or required under subpart E of this part.",
            NONE,
        ),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) <= 3 else "",
)
def test_the_researched_sentences(row, sentence, modality):
    assert classify(sentence) == modality, row


@pytest.mark.parametrize(
    "sentence",
    [
        # 800-53 PM-4: a concession is not the sentence's claim.
        "While plans of action and milestones are required for federal organizations, other "
        "types of organizations can help reduce risk by documenting and tracking planned "
        "remediations.",
        # 45 CFR 171.204(a): no subject in its clause, it continues "that:".
        "The actor cannot fulfill the request because it cannot segment the information from "
        "information that: (i) Is not permitted by applicable law to be made available.",
    ],
    ids=["800-53-PM-4", "171.204(a)"],
)
def test_catalog_sentences_the_measurement_caught_stay_unbound(sentence):
    """Found by running every sentence of the shipped catalogs through the
    draft of this fix: each read as binding, and must not."""
    assert classify(sentence) == NONE


def test_a_negative_subject_after_a_relative_word_states_no_requirement():
    """800-53 AC-20: "... may be such that no explicit terms and conditions
    are required." -- a permission (its "may"), not an obligation."""
    sentence = (
        "The trust relationships may be such that no explicit terms and conditions are required."
    )
    assert classify(sentence) == PERMISSION


def test_the_verb_twins_and_existing_phrases_read_as_before():
    assert classify("Users are required to use MFA.") == OBLIGATION
    assert classify("Sharing passwords is prohibited.") == PROHIBITION
    assert classify("Access is forbidden without approval.") == PROHIBITION
    assert classify("Access is permitted after approval.") == PERMISSION


def _branches(pattern) -> set[str]:
    """Every literal alternative of a `\\b(?:a|b c|...)\\b` pattern, read by
    Python's own regex parser -- not by slicing the source as the heading
    reader does, so the two can be compared."""
    import re._parser as parser

    def expand(items) -> list[str]:
        # The parser factors shared prefixes out of an alternation ("must
        # not|may" is `m` then a branch of "ust not" / "ay"), so every
        # string is rebuilt from the whole sequence, not from the branches.
        out = [""]
        for op, arg in items:
            if op is parser.LITERAL:
                options = [chr(arg)]
            elif op is parser.BRANCH:
                options = [s for branch in arg[1] for s in expand(branch)]
            elif op is parser.SUBPATTERN:
                options = expand(arg[-1])
            elif op is parser.IN:
                options = [chr(c) for kind, c in arg if kind is parser.LITERAL]
            elif op is parser.AT:
                continue
            else:
                raise AssertionError(f"not a plain phrase alternation: {op}")
            out = [prefix + option for prefix in out for option in options]
        return out

    return set(expand(parser.parse(pattern.pattern, pattern.flags)))


def test_the_heading_reader_sees_every_modal_phrase():
    """b5's #357 builds its heading-modal list by slicing the regex source of
    `_MODALITY_PATTERNS`. This change adds phrases there, so it is the change
    that could quietly drop some: every phrase the parser finds must be one
    the heading reader matches whole."""
    phrases = set().union(*(_branches(p) for _, p in _MODALITY_PATTERNS))
    assert len(phrases) > 30 and "is not permitted" in phrases and "is mandatory" in phrases
    missing = sorted(p for p in phrases if not _HEADING_MODAL_RE.fullmatch(p))
    assert missing == []


# 1d on #367: an aside between the subject and the predicate emptied the
# clause, and "no subject" then left a real requirement unbound.
_DASH = chr(0x2014)


@pytest.mark.parametrize(
    ("sentence", "modality"),
    [
        ("Written approval (see Appendix B) is required.", OBLIGATION),
        ("Encryption, where feasible, is required.", OBLIGATION),
        ("MFA, for all remote access, is required.", OBLIGATION),
        (f"USB devices {_DASH} including phones {_DASH} are not allowed.", PROHIBITION),
        ("USB devices -- including phones -- are not allowed.", PROHIBITION),
        # FedRAMP MA-5(1), recovered by the same fix in the catalog count.
        ("Only MA-5 (1) (a) (1) is required by FedRAMP Class C Baseline.", OBLIGATION),
        # ... and what must stay unbound still does.
        ("If approval is required, request it in the ticket.", NONE),
        ("Encryption (where required) protects data at rest.", NONE),
    ],
    ids=["paren", "comma-aside", "comma-aside-2", "em-dash-aside", "double-hyphen-aside",
         "fedramp-ma-5-1", "if-clause", "paren-condition"],
)  # fmt: skip
def test_an_aside_before_the_predicate_does_not_hide_its_subject(sentence, modality):
    assert classify(sentence) == modality


@pytest.mark.parametrize(
    "sentence",
    [
        "Whether logging is required depends on the system.",
        "Where training is required, the HR team records it.",
        "If testing is required, see the test plan.",
        "When patching is mandatory, the change board is informed.",
    ],
)
def test_a_condition_with_a_gerund_subject_stays_unbound(sentence):
    """1d on #367: a draft exempted an -ing word after a conditional (to read
    "When working remotely MFA is required" as binding) and so made these
    gerund-subject conditions into obligations. Unbound, as on the train."""
    assert classify(sentence) == NONE


@pytest.mark.xfail(strict=True, reason="named miss: a reduced clause reads as a condition")
def test_a_reduced_clause_before_the_predicate():
    """Known and accepted (1d on #367): the conditional rule cannot tell
    "When working remotely MFA is" from "When patching is", so the first
    stays unbound. Strict, so a fix that makes it bind is noticed."""
    assert classify("When working remotely MFA is required.") == OBLIGATION

"""Declared role labels are read verbatim as placeholders (#243).

"Governance, Risk and Compliance" (`teams.grc`) has a comma, which the
generic placeholder label class doesn't take, so `[Governance, Risk and
Compliance]` was never filled, even when configured, and never reported.

80's ruling (c), 4011: read each declared label from `org.roles` VERBATIM
beside the unchanged generic class. Not (a), widening the class to take a
comma, which would make every capitalised bracketed list in prose a
placeholder; not (b), renaming a user-visible label to suit the code.

The must-stay neighbours are written by hand (1d's allocation), not derived
from the role vocabulary: they're the prose shapes a comma-widening would
have swallowed.
"""

from __future__ import annotations

import pytest

from policyforge.org.context import (
    _PLACEHOLDER_RE,
    OrgProfile,
    apply_substitutions,
    load_org_profile,
)
from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES

GRC = "Governance, Risk and Compliance"
ROLES = {**VENDOR_ROLES, **TEAM_ROLES}


@pytest.mark.parametrize("label", sorted(role.label for role in ROLES.values()))
def test_every_declared_label_is_read_by_the_placeholder_pattern(label):
    """The guard #243 asked for: a declared label the pattern can't read is
    never filled, so the next label with a new character fails here."""
    match = _PLACEHOLDER_RE.fullmatch(f"[{label}]")
    assert match and (match.group(1) or match.group(2)) == label, label


def test_the_comma_label_is_filled_when_configured():
    profile = load_org_profile({"org": {"name": "X", "teams": {"grc": "Acme GRC"}}})
    result = apply_substitutions(f"Owned by [{GRC}].", profile)
    assert result.filled == [(GRC, "Acme GRC")]
    assert result.text == "Owned by Acme GRC."


def test_the_comma_label_is_reported_as_a_role_when_not_configured():
    result = apply_substitutions(f"Owned by [{GRC}].", OrgProfile())
    assert result.roles_left == [GRC] and result.unfilled == []


def test_the_comma_label_takes_a_hedge_like_any_role():
    """`[Role - interim]` resolves to the role, as the generic class does."""
    dash = chr(0x2013)
    profile = load_org_profile({"org": {"name": "X", "teams": {"grc": "Acme GRC"}}})
    result = apply_substitutions(f"Owned by [{GRC} {dash} interim].", profile)
    assert result.text == "Owned by Acme GRC."


#: Written by hand: what a comma in the generic class would newly have taken.
MUST_STAY = [
    "[Access Control, Audit]",  # a capitalised list in prose
    "[see A, B]",  # a reference list
    "[Governance, Risk]",  # the comma label's own prefix: not the label
    "[Governance, Risk and Compliance Office]",  # longer than the label
    # Lower-case first: the generic class never reads these either, so the
    # literals don't (1d on PR 249: case as the generic class has it).
    "[governance, risk and compliance]",
    "[security engineering]",
]


@pytest.mark.parametrize("span", MUST_STAY)
def test_a_bracketed_list_that_is_not_a_declared_label_stays_unmatched(span):
    result = apply_substitutions(f"See {span} for the rest.", OrgProfile())
    assert result.outstanding == [] and result.text == f"See {span} for the rest.", result


def test_the_generic_class_reads_what_it_read_before():
    """The literals sit beside the generic class, which is unchanged: a
    label it read is read the same, hedge included."""
    for span, label in [
        ("[Ticketing System]", "Ticketing System"),
        ("[Ticketing Systems]", "Ticketing Systems"),  # longer than a label: generic
        ("[On-Call Paging]", "On-Call Paging"),
        ("[CISO - interim]", "CISO"),
        ("[Access Review Frequency]", "Access Review Frequency"),
    ]:
        match = _PLACEHOLDER_RE.fullmatch(span)
        assert match and (match.group(1) or match.group(2)) == label, span


@pytest.mark.parametrize(
    "written",
    [
        "GOVERNANCE, RISK AND COMPLIANCE",
        "Governance, Risk And Compliance",
        "Governance, risk and compliance",
    ],
)
def test_the_comma_label_in_another_case_is_read_and_filled(written):
    """1d on PR 249: the fill pass looks a label up lowercased, so a model's
    `[GOVERNANCE, RISK AND COMPLIANCE]` must be read too, or #243's symptom
    survives in casing. The first character is as declared, the rest any
    case: the generic class's own rule (`[SECURITY ENGINEERING]` fills)."""
    match = _PLACEHOLDER_RE.fullmatch(f"[{written}]")
    assert match and (match.group(1) or match.group(2)) == written, written
    profile = load_org_profile({"org": {"name": "X", "teams": {"grc": "Acme GRC"}}})
    assert apply_substitutions(f"Owned by [{written}].", profile).text == "Owned by Acme GRC."
    assert apply_substitutions(f"Owned by [{written}].", OrgProfile()).roles_left == [written]


@pytest.mark.parametrize(
    ("span", "label"),
    [
        ("[Ops - Night]", "Ops - Night"),  # the longer label, not "Ops" hedged with "Night"
        ("[Ops - acting]", "Ops"),  # not a label: the shorter one, hedged
        ("[OPS - NIGHT]", "OPS - NIGHT"),  # case after the first character, as above
    ],
)
def test_the_longest_declared_label_wins_with_stand_in_labels(span, label):
    """b5 on PR 249: today's 47 labels have no label that is another plus a
    hedge shape, so the order can only be pinned with stand-ins. The label
    set is an input to `placeholder_pattern`."""
    from policyforge.org.context import _label_of, placeholder_pattern

    match = placeholder_pattern(["Ops", "Ops - Night"]).fullmatch(span)
    assert match and _label_of(match) == label, span

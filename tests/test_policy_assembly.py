"""`assemble_policy` over the drafts models write (22, part 2b; 1d's probe on
PR 445).

Every draft below is hand-written in a shape a model produces, not generated
from the patterns the assembler reads: numbered headings, nested headings, a
list headed another way, a tag no catalog holds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.content.tags import disk_scope
from policyforge.generate.policy_group import (
    CLOSING_HEADING,
    GovernedStandard,
    PolicyGroup,
    assemble_policy,
)
from policyforge.topics.registry import Policy

ROOT = Path("/content")


def _group(*titles, others=()):
    standards = [
        GovernedStandard(
            topic=title.removesuffix(" Standard"),
            title=title,
            owner="IAM",
            path=ROOT / "standards" / (title.lower().replace(" ", "-") + ".md"),
            relative="standards/x.md",
        )
        for title in titles
    ]
    return PolicyGroup(
        policy=Policy(name="Access Control", owner="CISO"),
        standards=standards,
        others=list(others),
    )


def _assemble(draft, group):
    return assemble_policy(draft, group, disk_scope(), link_from=ROOT / "policies")


def _headings(text):
    return [line for line in text.split("\n") if line.startswith("#")]


# ---- a Standard has its section however the draft headed it ---------------------

SHAPES = [
    "## Access Review Standard",
    "## 2. Access Review Standard",
    "## 2) Access Review Standard",
    "## 2.1 Access Review Standard",
    "## Section 2: Access Review Standard",
    "## II. Access Review Standard",
    "## **Access Review Standard**",
    "## `Access Review Standard`",
    "## ACCESS REVIEW STANDARD",
    "## Access Review Standard (summary)",
    "### Access Review Standard",
]


@pytest.mark.parametrize("heading", SHAPES)
def test_a_standards_section_is_found_however_the_draft_headed_it(heading):
    draft = f"# Policy\n\n## Purpose\n\nWhy.\n\n## Standards\n\n{heading}\n\nWe review access.\n"

    result = _assemble(draft, _group("Access Review Standard"))

    assert result.omitted == []
    # No stub: the model's own section is the only one, plus code's list.
    assert result.text.casefold().count("access review standard") == 2, result.text
    assert "This Policy governs the" not in result.text


def test_a_title_that_merely_starts_with_another_is_not_that_standard():
    draft = "# P\n\n## Access Review Standardisation\n\nText.\n"

    result = _assemble(draft, _group("Access Review Standard"))

    assert result.omitted == ["Access Review Standard"]


def test_a_title_carrying_digits_is_not_mistaken_for_numbering():
    draft = "# P\n\n## 802.1X Network Access Standard\n\nText.\n\n## 2FA Standard\n\nMore.\n"

    result = _assemble(draft, _group("802.1X Network Access Standard", "2FA Standard"))

    assert result.omitted == []


def test_a_standard_with_no_section_is_given_one_and_named_once():
    draft = "# P\n\n## Purpose\n\nWhy.\n\n## 2. Access Review Standard\n\nText.\n"

    result = _assemble(draft, _group("Access Review Standard", "Authentication Standard"))

    assert result.omitted == ["Authentication Standard"]
    assert result.text.count("## Authentication Standard\n\nThis Policy governs") == 1


# ---- one list, and it is code's ---------------------------------------------------

NAMED_LISTS = [
    "## Standards Governed by This Policy",
    "## Related Standards",
    "## 5. Applicable Standards",
    "## References",
]


@pytest.mark.parametrize("heading", NAMED_LISTS)
def test_a_list_of_standards_under_any_heading_is_dropped(heading):
    draft = (
        "# P\n\n## Access Review Standard\n\nText.\n\n"
        f"{heading}\n\n- Network Security Standard\n- Access Review Standard\n"
    )

    result = _assemble(
        draft, _group("Access Review Standard", others=["Network Security Standard"])
    )

    assert "Network Security Standard" not in result.text
    assert result.lists_dropped == [heading.removeprefix("## ")]
    # Only code's list remains.
    assert result.text.count(f"## {CLOSING_HEADING}") == 1


def test_a_numbered_or_linked_list_of_standards_is_dropped_too():
    draft = (
        "# P\n\n## Access Review Standard\n\nText.\n\n## Governed Documents\n\n"
        "1. [Network Security Standard](../standards/n.md)\n"
        "2) **Access Review Standard**\n"
    )

    result = _assemble(
        draft, _group("Access Review Standard", others=["Network Security Standard"])
    )

    assert "Network Security Standard" not in result.text
    assert result.lists_dropped == ["Governed Documents"]


def test_a_list_that_is_not_a_list_of_standards_is_kept():
    draft = (
        "# P\n\n## Scope\n\n- Employees\n- Contractors, as the Standards define them\n\n"
        "## Roles\n\n- The CISO is accountable\n- Managers apply it\n\n"
        "## Access Review Standard\n\nText.\n"
    )

    result = _assemble(draft, _group("Access Review Standard"))

    assert result.lists_dropped == []
    assert "- Employees" in result.text and "- The CISO is accountable" in result.text


def test_a_list_under_a_governed_standards_own_section_is_kept():
    draft = (
        "# P\n\n## Access Review Standard\n\nWe commit to:\n\n### Commitments\n\n"
        "- Follow the Access Review Standard\n- Report exceptions\n"
    )

    result = _assemble(draft, _group("Access Review Standard"))

    assert result.lists_dropped == []
    assert "- Report exceptions" in result.text


def test_a_standard_outside_the_group_named_in_prose_is_reported_not_edited():
    draft = (
        "# P\n\n## Access Review Standard\n\n"
        "Access is reviewed. See also the Network Security Standard.\n"
    )

    result = _assemble(
        draft, _group("Access Review Standard", others=["Network Security Standard"])
    )

    assert result.foreign == ["Network Security Standard"]
    assert "See also the Network Security Standard." in result.text


def test_a_governed_standard_named_in_prose_is_not_foreign():
    draft = "# P\n\n## Access Review Standard\n\nThis follows the Access Review Standard.\n"

    result = _assemble(
        draft, _group("Access Review Standard", others=["Network Security Standard"])
    )

    assert result.foreign == []


# ---- no tag-shaped span survives, and the ones no catalog holds are named -----------


def test_every_tag_shaped_span_is_removed_and_each_kind_named():
    draft = (
        "# P\n\n## Access Review Standard\n\n"
        "Accounts are reviewed. [NIST AC-2] Another claim [NIST AC-99] and one more "
        "[HITRUST 01.a] and `[NIST 800-53 AC-2(1)]`.\n"
    )

    result = _assemble(draft, _group("Access Review Standard"))

    for gone in ("[NIST AC-2]", "[NIST AC-99]", "[HITRUST 01.a]", "AC-2(1)"):
        assert gone not in result.text.split("## Standards under")[0], gone
    assert result.tags_unresolved == ["[NIST AC-99]", "[HITRUST 01.a]"]
    assert "[NIST AC-2]" in result.tags_removed


def test_a_role_placeholder_and_a_link_are_not_tags():
    draft = (
        "# P\n\n## Access Review Standard\n\n"
        "The [Security Team] reviews it; see [the register](https://example.org/r1).\n"
    )

    result = _assemble(draft, _group("Access Review Standard"))

    assert "[Security Team]" in result.text
    assert "[the register](https://example.org/r1)" in result.text
    assert result.tags_removed == [] and result.tags_unresolved == []


# ---- the closing list --------------------------------------------------------------


def test_the_closing_list_encodes_a_path_with_a_space():
    group = _group("Access Review Standard")
    group.standards[0].path = ROOT / "standards" / "Access Review.md"

    result = _assemble("# P\n\n## Access Review Standard\n\nText.\n", group)

    assert "[Access Review Standard](../standards/Access%20Review.md) — IAM" in result.text


def test_a_draft_that_is_all_one_list_still_gets_its_title_and_every_section():
    result = _assemble("- one\n- two\n", _group("Access Review Standard"))

    assert _headings(result.text)[0] == "# Access Control"
    assert result.omitted == ["Access Review Standard"]

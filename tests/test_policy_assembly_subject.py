"""A heading that names a Standard's subject without its document type, and a
list that only mentions a Standard (22, part 2b; 1d on PR 445, 6288).

Hand-written drafts: a model writing a Policy section heads it with the
subject (`## Access Review`), not `## Access Review Standard`.
"""

from __future__ import annotations

import pytest

from tests.test_policy_assembly import _assemble, _group

OTHERS = ["Incident Response Standard", "Vendor Risk Standard"]


@pytest.mark.parametrize(
    "heading",
    [
        "## Access Review",
        "## 2. Access Review",
        "## **Access Review**",
        "## ACCESS REVIEW",
        "## Access Reviews",
        "### Access Review",
    ],
)
def test_a_heading_naming_the_subject_is_the_standards_section(heading):
    draft = f"# Policy\n\n## Purpose\n\nWhy.\n\n## Standards\n\n{heading}\n\nWe review access.\n"

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert result.omitted == []
    assert "This Policy governs the" not in result.text
    assert "We review access." in result.text


def test_two_subject_headings_leave_no_stub_for_either():
    draft = (
        "# P\n\n## Purpose\n\nWhy.\n\n## Access Review\n\nWe review access.\n\n"
        "## Authentication\n\nWe verify people.\n"
    )

    result = _assemble(draft, _group("Access Review Standard", "Authentication Standard"))

    assert result.omitted == []
    assert result.text.count("## Access Review") == 1
    assert result.text.count("## Authentication") == 1


def test_a_subject_heading_of_a_standard_outside_the_group_is_dropped():
    draft = "# P\n\n## Access Review\n\nText.\n\n## Incident Response\n\nIncidents.\n"

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert "Incident" not in result.text
    assert result.lists_dropped == ["Incident Response"]


def test_a_longer_heading_that_starts_with_the_subject_is_not_the_standard():
    """`## Access Review Process` could be anything; only the title, or the
    title less its document type, is the Standard's."""
    draft = "# P\n\n## Access Review Process\n\nText.\n"

    result = _assemble(draft, _group("Access Review Standard"))

    assert result.omitted == ["Access Review Standard"]
    assert "## Access Review Process" in result.text


def test_a_roles_list_that_mentions_a_standard_is_kept():
    draft = (
        "# P\n\n## Access Review Standard\n\nText.\n\n## Roles and Responsibilities\n\n"
        "- Access Review Standard owner: IAM\n- Vendor Risk Standard owner: Procurement\n"
    )

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert result.lists_dropped == []
    assert "- Access Review Standard owner: IAM" in result.text


@pytest.mark.parametrize(
    "item",
    [
        "- Vendor Risk Standard",
        "- Vendor Risk Standard — Procurement",
        "- [Vendor Risk Standard](../standards/vendor.md): covers vendors",
        "1. **Vendor Risk Standard**",
        "- Physical Security Standard",
    ],
)
def test_a_list_of_references_to_standards_is_still_dropped(item):
    draft = f"# P\n\n## Access Review Standard\n\nText.\n\n## Governing Documents\n\n{item}\n"

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert result.lists_dropped == ["Governing Documents"]

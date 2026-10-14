"""Which lists the assembler drops (22, part 2b): only a list whose every item
is a reference to a Standard, and never under Purpose or Scope."""

from __future__ import annotations

from tests.test_policy_assembly import _assemble, _group

OTHERS = ["Vendor Risk Standard"]


def test_a_list_mixing_a_standard_with_other_items_is_kept():
    draft = (
        "# P\n\n## Access Review Standard\n\nText.\n\n## Escalation\n\n"
        "- Vendor Risk Standard\n- The CISO is told within a day\n"
    )

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert result.lists_dropped == []
    assert "- The CISO is told within a day" in result.text
    # The mention is still reported: it is a Standard this Policy does not govern.
    assert result.foreign == ["Vendor Risk Standard"]


def test_a_scope_that_is_a_list_of_the_governed_standards_is_kept():
    """Scope is a section the Policy is required to have, and a list of what
    it covers is a scope. It is not the closing list."""
    draft = (
        "# P\n\n## Purpose\n\nWhy.\n\n## Scope\n\n- Access Review Standard\n"
        "- Authentication Standard\n\n## Access Review Standard\n\nText.\n"
    )

    result = _assemble(draft, _group("Access Review Standard", "Authentication Standard"))

    assert result.lists_dropped == []
    assert result.text.count("- Authentication Standard") == 1
    assert "## Scope\n\n- Access Review Standard" in result.text


def test_the_same_list_under_another_heading_is_dropped():
    draft = (
        "# P\n\n## Purpose\n\nWhy.\n\n## Coverage\n\n- Access Review Standard\n"
        "- Authentication Standard\n\n## Access Review Standard\n\nText.\n"
    )

    result = _assemble(draft, _group("Access Review Standard", "Authentication Standard"))

    assert result.lists_dropped == ["Coverage"]

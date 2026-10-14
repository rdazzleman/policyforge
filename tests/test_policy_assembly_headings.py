"""A section headed as a Standard the Policy doesn't govern is dropped (22,
part 2b; ba on PR 445). Hand-written drafts in the shapes models produce."""

from __future__ import annotations

from tests.test_policy_assembly import _assemble, _group

OTHERS = ["Incident Response Standard", "Vendor Risk Standard"]


def _body(result):
    """The Policy above code's closing list."""
    return result.text.split("## Standards under this Policy")[0]


def test_a_section_headed_as_a_standard_outside_the_group_is_dropped_and_named():
    draft = (
        "# P\n\n## Access Review Standard\n\nWe review access.\n\n"
        "## Incident Response Standard\n\nIncidents are handled within a day.\n"
    )

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert "Incident Response" not in result.text
    assert result.lists_dropped == ["Incident Response Standard"]


def test_a_section_headed_as_a_standard_no_file_holds_is_dropped_too():
    draft = "# P\n\n## Access Review Standard\n\nText.\n\n## Physical Security Standard\n\nDoors.\n"

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert "Physical Security" not in result.text
    assert result.lists_dropped == ["Physical Security Standard"]


def test_a_numbered_heading_of_a_standard_outside_the_group_is_dropped():
    draft = "# P\n\n## Access Review Standard\n\nText.\n\n## 4. Vendor Risk Standard\n\nVendors.\n"

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert "Vendor Risk" not in result.text


def test_a_related_standards_list_with_prose_is_dropped():
    draft = (
        "# P\n\n## Access Review Standard\n\nText.\n\n## Related Standards\n\n"
        "The following apply too:\n\n- Vendor Risk Standard\n"
    )

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert "Vendor Risk" not in result.text
    assert result.lists_dropped == ["Related Standards"]


def test_a_plural_heading_for_a_governed_standard_is_that_standard():
    draft = "# P\n\n## Access Review Standards\n\nWe review access.\n"

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert result.omitted == [] and result.lists_dropped == []
    assert "We review access." in result.text


def test_other_sections_the_draft_adds_are_kept_and_that_is_the_limit():
    """The assembler drops sections headed as a Standard. It does not decide
    what else a Policy may say: an `Enforcement` section is the author's."""
    draft = (
        "# P\n\n## Access Review Standard\n\nText.\n\n## Enforcement\n\n"
        "Violations are handled under the disciplinary process.\n"
    )

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert "## Enforcement" in _body(result)
    assert result.lists_dropped == []


def test_a_nested_heading_of_a_standard_outside_the_group_is_dropped_with_its_text():
    draft = (
        "# P\n\n## Standards\n\n### Access Review Standard\n\nWe review access.\n\n"
        "### Incident Response Standard\n\nIncidents are handled.\n\n## Purpose\n\nWhy.\n"
    )

    result = _assemble(draft, _group("Access Review Standard", others=OTHERS))

    assert "Incident" not in result.text
    assert "We review access." in result.text
    assert result.omitted == []

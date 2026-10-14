"""Drift reports changes to nested level requirements (#442).

`drift` compared control statements, but missed nested level requirements
beneath them (`control.requirements`). HITRUST and other levelled catalogs
carry their substance in level requirements rather than control headings,
so a reworded or removed requirement reported clean while leaving documents
and synthesis out of date.

Invariant:
Every change to anything the catalog carries that a document can cite or that
synthesis reads (text, presence, or authoritative-source mappings, at every level
of nesting) is reported, and nothing else is. The unit compared is the citable
requirement, whatever depth the loader puts it at. No HITRUST special casing:
derived from catalog structure, so GovRAMP, SOC 2 or synthetic levelled
catalogs get the same coverage.

Tests use hand-written, invented catalogs only. Never read `local_content/`
or any licensed catalog.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from policyforge.frameworks.drift import (
    ADDED,
    CHANGED,
    REMOVED,
    analyze_drift,
    diff_catalogs,
)
from policyforge.ingest.schema import Control, Requirement

FRAMEWORK = "Synthetic-Levelled"


def _req(
    req_id: str = "01.e Level 1",
    level: str = "Level 1",
    statement: str = "Invented level requirement text.",
    mappings: dict[str, list[str]] | None = None,
) -> Requirement:
    return Requirement(
        requirement_id=req_id,
        level=level,
        statement=statement,
        mappings=dict(mappings or {}),
    )


def _control(
    control_id: str = "01.e",
    statement: str = "Invented control statement.",
    framework: str = FRAMEWORK,
    requirements: list[Requirement] | None = None,
    **kwargs,
) -> Control:
    return Control(
        control_id=control_id,
        title=kwargs.pop("title", f"{control_id} title"),
        framework=framework,
        framework_version=kwargs.pop("version", "v1.0"),
        control_statement=statement,
        requirements=list(requirements if requirements is not None else [_req()]),
        **kwargs,
    )


# --------------------------------------------------------------------------
# Must REPORT cases (arm: fail on today's drift)
# --------------------------------------------------------------------------


def test_a_reworded_level_requirement_is_reported():
    old = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Original wording.")])]
    new = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Reworded upstream.")])]

    changes = diff_catalogs(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert (change.control_id, change.kind) == ("01.e Level 1", CHANGED)
    assert "statement" in change.fields
    assert change.substantive
    assert change.level == "Level 1"
    assert "Reworded upstream" in change.detail


def test_a_removed_level_requirement_is_reported():
    req1 = _req("01.f Level 1", "Level 1", "Keep me.")
    req2 = _req("01.f Level 2", "Level 2", "Remove me.")
    old = [_control("01.f", requirements=[req1, req2])]
    new = [_control("01.f", requirements=[req1])]

    changes = diff_catalogs(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert (change.control_id, change.kind) == ("01.f Level 2", REMOVED)
    assert change.substantive
    assert change.level == "Level 2"


def test_an_added_level_requirement_is_reported():
    req1 = _req("01.f Level 1", "Level 1", "Existing.")
    req2 = _req("01.f Level 2", "Level 2", "Newly arrived.")
    old = [_control("01.f", requirements=[req1])]
    new = [_control("01.f", requirements=[req1, req2])]

    changes = diff_catalogs(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert (change.control_id, change.kind) == ("01.f Level 2", ADDED)
    assert change.substantive
    assert change.level == "Level 2"


def test_a_mapping_added_on_a_level_requirement_is_reported():
    old = [
        _control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Statement.", mappings={})])
    ]
    new = [
        _control(
            "01.e",
            requirements=[
                _req(
                    "01.e Level 1",
                    "Level 1",
                    "Statement.",
                    mappings={"NIST SP 800-53": ["AC-2"]},
                )
            ],
        )
    ]

    changes = diff_catalogs(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert (change.control_id, change.kind) == ("01.e Level 1", CHANGED)
    assert "mappings" in change.fields
    assert change.substantive
    assert "AC-2" in change.detail


def test_a_mapping_removed_on_a_level_requirement_is_reported():
    old = [
        _control(
            "01.e",
            requirements=[
                _req(
                    "01.e Level 1",
                    "Level 1",
                    "Statement.",
                    mappings={"NIST SP 800-53": ["AC-2", "AC-3"]},
                )
            ],
        )
    ]
    new = [
        _control(
            "01.e",
            requirements=[
                _req(
                    "01.e Level 1",
                    "Level 1",
                    "Statement.",
                    mappings={"NIST SP 800-53": ["AC-2"]},
                )
            ],
        )
    ]

    changes = diff_catalogs(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert (change.control_id, change.kind) == ("01.e Level 1", CHANGED)
    assert "mappings" in change.fields
    assert change.substantive
    assert "AC-3" in change.detail


def test_a_requirement_moved_from_one_level_to_another_is_reported_when_id_is_stable():
    old = [_control("01.e", requirements=[_req("REQ-01", "Level 1", "Requirement text.")])]
    new = [_control("01.e", requirements=[_req("REQ-01", "Level 2", "Requirement text.")])]

    changes = diff_catalogs(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert (change.control_id, change.kind) == ("REQ-01", CHANGED)
    assert "level" in change.fields
    assert change.substantive
    assert "Level 1 -> Level 2" in change.detail


def test_a_requirement_moved_from_one_level_to_another_is_reported_when_id_carries_level():
    old = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Requirement text.")])]
    new = [_control("01.e", requirements=[_req("01.e Level 2", "Level 2", "Requirement text.")])]

    changes = {(c.control_id, c.kind): c for c in diff_catalogs(old, new)}
    assert ("01.e Level 1", REMOVED) in changes
    assert ("01.e Level 2", ADDED) in changes
    assert changes[("01.e Level 1", REMOVED)].substantive
    assert changes[("01.e Level 2", ADDED)].substantive


# --------------------------------------------------------------------------
# Must NOT report as substantive
# --------------------------------------------------------------------------


def test_whitespace_and_line_ending_changes_on_level_requirement_are_not_reported():
    old = [
        _control(
            "01.e",
            requirements=[_req("01.e Level 1", "Level 1", "Invented text line.\nSecond line.")],
        )
    ]
    new = [
        _control(
            "01.e",
            requirements=[
                _req("01.e Level 1", "Level 1", "  Invented  text  line.\r\nSecond   line.  ")
            ],
        )
    ]

    changes = diff_catalogs(old, new)
    assert changes == []


def test_the_same_catalog_compared_with_itself_reports_nothing():
    controls = [
        _control(
            "01.e",
            requirements=[
                _req("01.e Level 1", "Level 1", "Statement 1."),
                _req("01.e Level 2", "Level 2", "Statement 2."),
            ],
        ),
        _control(
            "01.f",
            requirements=[
                _req("01.f Level 1", "Level 1", "Statement 3."),
            ],
        ),
    ]

    report = analyze_drift(controls, copy.deepcopy(controls))
    assert not report.needs_review
    assert report.changes == []
    assert "Nothing to review" in report.format_report()


# --------------------------------------------------------------------------
# The exact defect table scenario from issue #442 Done When
# --------------------------------------------------------------------------


def test_the_three_table_edits_are_reported_with_id_and_level_named():
    """Done when:
    | Edit | Reported? |
    | Reworded a level requirement (`01.e Level 1`) | Yes |
    | Removed a level requirement (`01.f Level 2`) | Yes |
    | Reworded a control statement (`01.g`) | Yes |
    """
    old = [
        _control(
            "01.e",
            statement="Heading 01.e",
            requirements=[_req("01.e Level 1", "Level 1", "Original L1.")],
        ),
        _control(
            "01.f",
            statement="Heading 01.f",
            requirements=[
                _req("01.f Level 1", "Level 1", "Original 01.f L1."),
                _req("01.f Level 2", "Level 2", "Original 01.f L2."),
            ],
        ),
        _control("01.g", statement="Original control statement 01.g", requirements=[]),
    ]

    new = [
        _control(
            "01.e",
            statement="Heading 01.e",
            requirements=[_req("01.e Level 1", "Level 1", "Reworded L1.")],
        ),
        _control(
            "01.f",
            statement="Heading 01.f",
            requirements=[
                _req("01.f Level 1", "Level 1", "Original 01.f L1."),
            ],
        ),
        _control("01.g", statement="Reworded control statement 01.g", requirements=[]),
    ]

    changes = diff_catalogs(old, new)
    by_id = {c.control_id: c for c in changes}

    assert len(changes) == 3
    assert set(by_id) == {"01.e Level 1", "01.f Level 2", "01.g"}

    # Reworded level requirement
    c_reworded = by_id["01.e Level 1"]
    assert c_reworded.kind == CHANGED
    assert c_reworded.level == "Level 1"
    assert "statement" in c_reworded.fields

    # Removed level requirement
    c_removed = by_id["01.f Level 2"]
    assert c_removed.kind == REMOVED
    assert c_removed.level == "Level 2"

    # Reworded control statement
    c_ctrl = by_id["01.g"]
    assert c_ctrl.kind == CHANGED
    assert "control_statement" in c_ctrl.fields


# --------------------------------------------------------------------------
# Blast radius: documents citing level requirements
# --------------------------------------------------------------------------


def _create_tree(tmp_path: Path, **documents) -> Path:
    root = tmp_path / "docs" / "standards"
    root.mkdir(parents=True)
    for name, body in documents.items():
        (root / f"{name}.md").write_text(body, encoding="utf-8")
    return tmp_path / "docs"


def test_blast_radius_works_for_level_requirements(tmp_path):
    root = _create_tree(
        tmp_path,
        access="# Access Control\n\nMust authenticate. [Synthetic-Levelled 01.e Level 1]\n",
        unrelated="# Other\n\nOther thing. [Synthetic-Levelled 02.a]\n",
    )

    old = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Old statement.")])]
    new = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "New statement.")])]

    report = analyze_drift(old, new, content_root=root)
    assert report.affected_documents == ["standards/access.md"]
    assert report.impacts["01.e Level 1"].documents == ["standards/access.md"]


def test_blast_radius_works_when_document_cites_parent_control(tmp_path):
    """A document citing the parent control is reached by a change to its level requirement."""
    root = _create_tree(
        tmp_path,
        access="# Access Control\n\nCites control heading. [Synthetic-Levelled 01.e]\n",
    )

    old = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Old statement.")])]
    new = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "New statement.")])]

    report = analyze_drift(old, new, content_root=root)
    assert report.affected_documents == ["standards/access.md"]
    assert report.impacts["01.e Level 1"].documents == ["standards/access.md"]


def test_blast_radius_through_crosswalk_for_level_requirements(tmp_path):
    """Crosswalk maps NIST IR-4 to 01.e Level 1: a document citing IR-4 is reached."""
    from policyforge.mapping.crosswalk import build_crosswalk

    root = _create_tree(
        tmp_path,
        incidents="# Incidents\n\nHandled per plan. [NIST IR-4]\n",
    )

    old_nist = Control("IR-4", "Incident Handling", "NIST-800-53", "r5")
    old = [
        _control(
            "01.e",
            requirements=[
                _req(
                    "01.e Level 1",
                    "Level 1",
                    "Old statement.",
                    mappings={"NIST SP 800-53": ["IR-4"]},
                )
            ],
        ),
    ]
    new = [
        _control(
            "01.e",
            requirements=[
                _req(
                    "01.e Level 1",
                    "Level 1",
                    "New statement.",
                    mappings={"NIST SP 800-53": ["IR-4"]},
                )
            ],
        ),
    ]

    crosswalk = build_crosswalk([old_nist, *new])
    report = analyze_drift(
        old,
        new,
        content_root=root,
        crosswalk=crosswalk,
        catalogs={"nist-800-53": {"IR-4"}},
    )
    assert report.affected_documents == ["standards/incidents.md"]


# --------------------------------------------------------------------------
# No HITRUST special casing: GovRAMP and SOC 2 catalogs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("framework", ["GovRAMP", "SOC-2"])
def test_nested_requirements_supported_without_hitrust_special_casing(framework):
    old = [
        _control(
            "SEC-01",
            framework=framework,
            requirements=[
                _req("SEC-01 Item A", "Item A", "Old rule.", mappings={}),
            ],
        )
    ]
    new = [
        _control(
            "SEC-01",
            framework=framework,
            requirements=[
                _req("SEC-01 Item A", "Item A", "New rule.", mappings={}),
            ],
        )
    ]

    changes = diff_catalogs(old, new)
    assert len(changes) == 1
    assert (changes[0].control_id, changes[0].kind) == ("SEC-01 Item A", CHANGED)
    assert changes[0].level == "Item A"
    assert changes[0].substantive


# --------------------------------------------------------------------------
# CLI surface: policyforge drift
# --------------------------------------------------------------------------


def test_cli_drift_reports_level_requirements_and_fails_on_change(tmp_path):
    import dataclasses
    import json

    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    old = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Old statement.")])]
    new = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "New statement.")])]

    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps([dataclasses.asdict(c) for c in old]), encoding="utf-8")
    new_path.write_text(json.dumps([dataclasses.asdict(c) for c in new]), encoding="utf-8")

    args = ["drift", "--controls", str(new_path), "--old", str(old_path), "--fail-on-change"]
    result = CliRunner().invoke(cli_mod.cli, args)
    assert result.exit_code == 1
    assert "01.e Level 1" in result.output
    assert "Worth reading" in result.output


def test_cli_drift_unchanged_levelled_catalog_passes(tmp_path):
    import dataclasses
    import json

    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    controls = [_control("01.e", requirements=[_req("01.e Level 1", "Level 1", "Statement.")])]
    cat_path = tmp_path / "cat.json"
    cat_path.write_text(json.dumps([dataclasses.asdict(c) for c in controls]), encoding="utf-8")

    args = ["drift", "--controls", str(cat_path), "--old", str(cat_path), "--fail-on-change"]
    result = CliRunner().invoke(cli_mod.cli, args)
    assert result.exit_code == 0
    assert "Nothing to review" in result.output


# --------------------------------------------------------------------------
# Every field the schema carries, at every level (ba's carry of 442)
# --------------------------------------------------------------------------

import dataclasses  # noqa: E402

from policyforge.frameworks.drift import COMPARED, FIELDS, SUBSTANTIVE_FIELDS  # noqa: E402
from policyforge.ingest.schema import ControlEnhancement  # noqa: E402

#: The schema's own fields, read from the dataclasses: the population is the
#: schema, never drift's table, so a new field can't be left out of both.
SCHEMA = {cls.__name__: cls for cls in (Control, ControlEnhancement, Requirement)}
KEYS = {"control_id", "enhancement_id", "requirement_id"}
#: Containers each have their own tests above (presence of an enhancement or
#: a requirement).
CONTAINERS = {("Control", "enhancements"), ("Control", "requirements")}


def test_every_schema_field_is_classified():
    for name, cls in SCHEMA.items():
        assert {f.name for f in dataclasses.fields(cls)} == set(FIELDS[name]), name


def _full_catalog():
    """One control with one enhancement and one requirement, every field set."""
    requirement = Requirement(
        requirement_id="01.e Level 1",
        level="Level 1",
        statement="Invented level requirement text.",
        organizational_factors=["Invented factor"],
        system_factors=["Invented factor"],
        regulatory_factors=["Invented factor"],
        mappings={"Invented Source": ["X-1"]},
    )
    enhancement = ControlEnhancement(
        enhancement_id="01.e(1)",
        title="Invented enhancement",
        baseline="Moderate",
        description="Invented enhancement text.",
        parameter_values={"01.e(1)_prm_1": "annually"},
        additional_requirements="Invented additional requirement.",
        source_crosswalk={"Invented Source": "X-2"},
    )
    control = Control(
        control_id="01.e",
        title="Invented control",
        framework=FRAMEWORK,
        framework_version="v1.0",
        family="Invented family",
        family_abbr="IF",
        baseline="Moderate",
        control_statement="Invented control statement.",
        discussion="Invented discussion.",
        enhancements=[enhancement],
        related_controls=["01.f"],
        source_crosswalk={"Invented Source": "X-3"},
        source_path="invented/path",
        parameter_values={"01.e_prm_1": "monthly"},
        additional_requirements="Invented additional requirement.",
        objective_id="obj-1",
        objective_title="Invented objective",
        objective_statement="Invented objective statement.",
        factor_type="Invented factor type",
        requirements=[requirement],
    )
    return [control]


def _changed(value):
    if isinstance(value, dict):
        return {**value, "Added": "Invented added value"}
    if isinstance(value, list):
        return [*value, "Invented added item"]
    return f"{value or ''} changed".strip()


CASES = [
    (name, f.name)
    for name, cls in SCHEMA.items()
    for f in dataclasses.fields(cls)
    if f.name not in KEYS and (name, f.name) not in CONTAINERS
]


@pytest.mark.parametrize(("level", "name"), CASES, ids=[f"{lv}.{n}" for lv, n in CASES])
def test_each_field_is_reported_exactly_as_it_is_classified(level, name):
    """A change to one field, at one level, of an invented catalog."""
    old = _full_catalog()
    new = copy.deepcopy(old)
    target = {
        "Control": new[0],
        "ControlEnhancement": new[0].enhancements[0],
        "Requirement": new[0].requirements[0],
    }[level]
    setattr(target, name, _changed(getattr(target, name)))
    changes = diff_catalogs(old, new)

    handling, reported_as = FIELDS[level][name]
    if handling == COMPARED:
        assert any(reported_as in c.fields for c in changes), (level, name, changes)
        assert reported_as in SUBSTANTIVE_FIELDS or reported_as in (
            "title",
            "discussion",
            "related_controls",
            "family",
        )
    else:
        assert changes == [], (level, name, changes)


@pytest.mark.parametrize(
    ("level", "edit"),
    [
        ("Control", lambda c: setattr(c, "source_crosswalk", {"Invented Source": "X-9"})),
        ("ControlEnhancement", lambda e: setattr(e, "source_crosswalk", {})),
        ("Control", lambda c: setattr(c, "additional_requirements", "Reworded.")),
        (
            "ControlEnhancement",
            lambda e: setattr(e, "parameter_values", {"01.e(1)_prm_1": "weekly"}),
        ),
    ],
)
def test_a_mapping_or_requirement_change_above_the_levels_is_substantive(level, edit):
    """Mappings and additional requirements at the control and enhancement
    levels weren't compared before 442's carry; a parameter's VALUE wasn't
    either (only its id)."""
    old = _full_catalog()
    new = copy.deepcopy(old)
    edit(new[0] if level == "Control" else new[0].enhancements[0])
    changes = diff_catalogs(old, new)
    assert changes and all(set(c.fields) & set(SUBSTANTIVE_FIELDS) for c in changes), changes


def test_whitespace_only_changes_above_the_levels_stay_unreported():
    old = _full_catalog()
    new = copy.deepcopy(old)
    new[0].source_crosswalk = {" Invented Source ": "X-3 "}
    new[0].additional_requirements = "Invented  additional\r\nrequirement."
    new[0].enhancements[0].parameter_values = {"01.e(1)_prm_1": " annually "}
    assert diff_catalogs(old, new) == []


def test_a_declared_family_is_kept_for_a_control_with_requirements(tmp_path):
    """Indexing level requirements into families never overrides the family a
    catalog declares for their control: here 01.e is declared to be in 01.x's
    family, so a document citing 01.x is reached by a change to 01.e Level 1."""
    from policyforge.mapping.crosswalk import normalize_framework

    root = _create_tree(
        tmp_path, access="# Access\n\nCites the family. [Synthetic-Levelled 01.x]\n"
    )
    sibling = _control("01.x", requirements=[])
    old = [_control("01.e"), sibling]
    reworded = [_req(statement="Reworded requirement text.")]
    new = [_control("01.e", requirements=reworded), sibling]
    declared = {normalize_framework(FRAMEWORK): {"01.e": "01.x", "01.x": "01.x"}}
    report = analyze_drift(old, new, content_root=root, families=declared, family_links={})
    assert report.impacts["01.e Level 1"].documents == ["standards/access.md"]


@pytest.mark.parametrize(
    "edit",
    [
        lambda c: setattr(c, "source_crosswalk", {"Invented Source": "X-3,X-4"}),
        lambda c: setattr(c.enhancements[0], "source_crosswalk", {"Invented Source": " X-2 ; X-5"}),
        lambda c: setattr(c.requirements[0], "mappings", {"Invented Source": ["X-6", "X-1"]}),
    ],
    ids=["control", "enhancement", "requirement"],
)
def test_mapping_order_and_spacing_are_not_changes_at_any_level(edit):
    """The same ids, reordered or spaced differently, at every level (b5 on
    PR 457): no change. The `before` side holds them in the other order."""
    old = _full_catalog()
    old[0].source_crosswalk = {"Invented Source": "X-4, X-3"}
    old[0].enhancements[0].source_crosswalk = {"Invented Source": "X-5,X-2"}
    old[0].requirements[0].mappings = {"Invented Source": ["X-1", "X-6"]}
    new = copy.deepcopy(old)
    edit(new[0])
    assert diff_catalogs(old, new) == []


def test_a_mapping_id_added_in_a_string_is_a_change():
    old = _full_catalog()
    new = copy.deepcopy(old)
    new[0].source_crosswalk = {"Invented Source": "X-3, X-9"}
    [change] = diff_catalogs(old, new)
    assert "mappings" in change.fields and "X-9" in change.detail

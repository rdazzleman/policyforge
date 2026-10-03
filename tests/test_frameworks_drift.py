"""Framework-version drift: what changed, and what it reaches.

The question a catalog bump raises is not "what is different" — a diff
answers that and is unreadable — but "what do I have to go and look at?".
So the tests here are mostly about *scoping*: that an editorial reword does
not drag a document into review, that a change to an enhancement reaches the
topic anchoring its parent, and that a control nobody has written about says
so rather than looking like work.

The two wrong answers this exists to prevent are regenerating everything
(which discards every hand edit and review the documents ever had) and
changing nothing (which lets documents quietly stop matching the catalog
they cite).
"""

from __future__ import annotations

import json
from pathlib import Path

from policyforge.frameworks.drift import (
    ADDED,
    CHANGED,
    REMOVED,
    analyze_drift,
    diff_catalogs,
    documents_citing,
)
from policyforge.ingest.schema import Control, ControlEnhancement
from policyforge.parameters.ledger import Decision
from policyforge.topics.registry import Topic


def _control(control_id="AC-2", statement="a. Do the thing.", **kwargs):
    return Control(
        control_id=control_id,
        title=kwargs.pop("title", f"{control_id} title"),
        framework="NIST-800-53",
        framework_version=kwargs.pop("version", "Rev 5 (5.1.1)"),
        baseline=kwargs.pop("baseline", "Low, Moderate, High"),
        control_statement=statement,
        discussion=kwargs.pop("discussion", ""),
        related_controls=kwargs.pop("related_controls", []),
        enhancements=[
            ControlEnhancement(enhancement_id=e, title="e", baseline="Moderate", description="d")
            for e in kwargs.pop("enhancements", [])
        ],
    )


# --------------------------------------------------------------------------
# What changed
# --------------------------------------------------------------------------


def test_a_new_control_is_reported_as_added():
    changes = diff_catalogs([_control("AC-2")], [_control("AC-2"), _control("AC-99")])

    assert [(c.control_id, c.kind) for c in changes] == [("AC-99", ADDED)]


def test_a_withdrawn_control_is_reported_as_removed():
    changes = diff_catalogs([_control("AC-2"), _control("AC-4")], [_control("AC-2")])

    assert [(c.control_id, c.kind) for c in changes] == [("AC-4", REMOVED)]


def test_a_changed_requirement_names_the_field_and_shows_the_lines():
    changes = diff_catalogs(
        [_control(statement="a. Review accounts annually.")],
        [_control(statement="a. Review accounts quarterly.")],
    )

    assert changes[0].kind == CHANGED
    assert "control_statement" in changes[0].fields
    assert "quarterly" in changes[0].detail
    assert changes[0].substantive


def test_reflowed_whitespace_is_not_a_change():
    """A reflow is not a new obligation, and reporting it as one is how a
    drift report becomes something people skim past."""
    changes = diff_catalogs(
        [_control(statement="a. Review   accounts\nannually.")],
        [_control(statement="a. Review accounts annually.")],
    )

    assert changes == []


def test_a_discussion_edit_is_editorial_not_substantive():
    changes = diff_catalogs(
        [_control(discussion="Old wording.")],
        [_control(discussion="New wording, same meaning.")],
    )

    assert changes[0].fields == ["discussion"]
    assert not changes[0].substantive


def test_a_baseline_move_is_substantive():
    """It changes which controls an SSP has to answer for."""
    changes = diff_catalogs([_control(baseline="Low, Moderate, High")], [_control(baseline="High")])

    assert "baseline" in changes[0].fields
    assert changes[0].substantive
    assert "High" in changes[0].detail


def test_gaining_an_enhancement_is_substantive():
    changes = diff_catalogs(
        [_control(enhancements=["AC-2(1)"])], [_control(enhancements=["AC-2(1)", "AC-2(2)"])]
    )

    assert "enhancements" in changes[0].fields
    assert changes[0].substantive


def test_a_parameter_appearing_or_vanishing_is_substantive():
    """A requirement that stopped being organization-defined is a decision
    you no longer get to make, and one that started being it is a decision
    you now owe."""
    changes = diff_catalogs(
        [_control(statement="a. Review [Assignment: organization-defined frequency].")],
        [_control(statement="a. Review quarterly.")],
    )

    assert "parameters" in changes[0].fields


def test_an_unchanged_catalog_reports_nothing():
    assert diff_catalogs([_control()], [_control()]) == []


# --------------------------------------------------------------------------
# What it reaches
# --------------------------------------------------------------------------


def _tree(tmp_path: Path, **documents) -> Path:
    root = tmp_path / "docs" / "standards"
    root.mkdir(parents=True)
    for name, body in documents.items():
        (root / f"{name}.md").write_text(body, encoding="utf-8")
    return tmp_path / "docs"


def _citing(controls, root):
    """Document reach in 800-53, the catalog these documents cite (#377:
    reach is within the change's own framework)."""
    return documents_citing(
        controls, root, framework="NIST 800-53", catalog_ids={"AC-2", "AC-2(3)", "CP-9"}
    )


def test_a_document_citing_the_control_is_found(tmp_path):
    root = _tree(tmp_path, access="# A\n\nRecertified. [NIST AC-2 | HIPAA 164.308(a)(4)]\n")

    hits = _citing({"AC-2"}, root)

    assert hits["AC-2"] == ["standards/access.md"]


def test_a_document_citing_the_parent_is_reached_by_an_enhancement_change(tmp_path):
    """A reader who has to re-check AC-2(3) has to re-check AC-2."""
    root = _tree(tmp_path, access="# A\n\nText. [NIST AC-2]\n")

    hits = _citing({"AC-2(3)"}, root)

    assert hits["AC-2(3)"] == ["standards/access.md"]


def test_a_document_citing_something_else_is_left_alone(tmp_path):
    root = _tree(tmp_path, backup="# B\n\nText. [NIST CP-9]\n")

    assert _citing({"AC-2"}, root) == {}


def test_a_missing_content_tree_is_not_an_error(tmp_path):
    assert _citing({"AC-2"}, tmp_path / "nope") == {}


def test_a_change_reaches_the_topic_that_anchors_the_control(tmp_path):
    report = analyze_drift(
        [_control(statement="a. Annually.")],
        [_control(statement="a. Quarterly.")],
        topics=[Topic(name="Access Review", owner="IAM", nist_controls=["AC-2"])],
    )

    assert report.affected_topics == ["Access Review"]


def test_an_enhancement_change_reaches_the_topic_anchoring_its_parent():
    """An anchor claims its enhancements, the same rule coverage.py uses."""
    report = analyze_drift(
        [_control("AC-2(3)", statement="a. Annually.")],
        [_control("AC-2(3)", statement="a. Quarterly.")],
        topics=[Topic(name="Access Review", owner="IAM", nist_controls=["AC-2"])],
    )

    assert report.affected_topics == ["Access Review"]


def test_a_change_reaches_a_parameter_decided_on_the_old_wording():
    """The decision was made against text that has since moved, which is
    exactly the thing nobody notices."""
    report = analyze_drift(
        [_control(statement="a. Review [Assignment: organization-defined frequency].")],
        [_control(statement="a. Review at least every 90 days.")],
        decisions={"AC-2/frequency": Decision(value="quarterly")},
    )

    assert report.affected_parameters == ["AC-2/frequency"]


def test_an_editorial_change_drags_nothing_into_review(tmp_path):
    """The noise-reduction that makes the report worth reading."""
    root = _tree(tmp_path, backup="# B\n\nText. [NIST CP-9]\n")
    report = analyze_drift(
        [_control("CP-9", discussion="Old.")],
        [_control("CP-9", discussion="New.")],
        topics=[Topic(name="Backup", owner="Platform", nist_controls=["CP-9"])],
        content_root=root,
    )

    assert report.affected_documents == []
    assert report.affected_topics == []
    assert not report.needs_review
    assert len(report.editorial) == 1


def test_a_new_control_nobody_has_written_about_says_so():
    report = analyze_drift([], [_control("AC-99")], topics=[])

    assert report.needs_review
    assert "reaches nothing you have written yet" in report.format_report()


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def test_no_change_says_so_plainly():
    report = analyze_drift([_control()], [_control()])

    assert "Nothing to review" in report.format_report()
    assert not report.needs_review


def test_the_report_names_the_versions():
    report = analyze_drift(
        [_control(version="Rev 5 (5.1.1)", statement="a. Old.")],
        [_control(version="Rev 5 (5.2.0)", statement="a. New.")],
    )

    assert "Rev 5 (5.1.1) -> Rev 5 (5.2.0)" in report.format_report()


def test_the_report_separates_what_is_worth_reading(tmp_path):
    report = analyze_drift(
        [_control("AC-2", statement="a. Annually."), _control("CP-9", discussion="Old.")],
        [_control("AC-2", statement="a. Quarterly."), _control("CP-9", discussion="New.")],
    )
    text = report.format_report()

    assert "Worth reading:" in text
    assert "Editorial only (1)" in text
    assert len(report.substantive) == 1


def test_the_report_warns_against_regenerating_everything(tmp_path):
    root = _tree(tmp_path, access="# A\n\nText. [NIST AC-2]\n")
    report = analyze_drift(
        [_control(statement="a. Annually.")],
        [_control(statement="a. Quarterly.")],
        content_root=root,
    )

    assert "discard every hand edit" in report.format_report()


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def _write_catalog(path: Path, controls):
    import dataclasses

    path.write_text(json.dumps([dataclasses.asdict(c) for c in controls]), encoding="utf-8")
    return path


def test_the_drift_command_reports_and_can_fail_the_build(tmp_path):
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    old = _write_catalog(tmp_path / "old.json", [_control(statement="a. Annually.")])
    new = _write_catalog(tmp_path / "new.json", [_control(statement="a. Quarterly.")])
    args = ["drift", "--controls", str(new), "--old", str(old), "--topics", str(tmp_path / "x")]

    result = CliRunner().invoke(cli_mod.cli, args)
    assert result.exit_code == 0
    assert "Worth reading" in result.output

    failing = CliRunner().invoke(cli_mod.cli, [*args, "--fail-on-change"])
    assert failing.exit_code == 1


def test_an_unchanged_catalog_passes_the_build(tmp_path):
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    same = _write_catalog(tmp_path / "c.json", [_control()])

    result = CliRunner().invoke(
        cli_mod.cli,
        ["drift", "--controls", str(same), "--old", str(same), "--fail-on-change"],
    )

    assert result.exit_code == 0
    assert "Nothing to review" in result.output


def test_without_a_baseline_to_compare_against_it_says_what_to_do(tmp_path):
    """A catalog that was never committed has no previous version, and the
    fix is to commit this one so the next update has something to diff."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    catalog = _write_catalog(tmp_path / "untracked.json", [_control()])

    result = CliRunner().invoke(cli_mod.cli, ["drift", "--controls", str(catalog)])

    assert result.exit_code != 0
    assert "commit the current one" in result.output

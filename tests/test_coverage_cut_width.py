"""Coverage names HITRUST level labels left at the report's width (#27), with
no parser installed.

These tests lived with the HITRUST parser's own (`test_hitrust_cut_width.py`),
which left this tree for the licensed-framework plugin (#32, part 3). What
they pin is public: `coverage` measures the labels from the level rules in
`ingest/levelled.py`, so the field never goes silently empty for want of
the parser (1d on #34). Every label here is invented. None is HITRUST's.
"""

from __future__ import annotations

import dataclasses
import json

from policyforge.ingest import levelled
from policyforge.ingest.schema import MATURITY, OVERLAY, Control, Requirement

AT_36 = "Level Invented Regional Data Protect"  # 36: at the cut width
BELOW = "Level Invented Data Protection Act"  # 34: whole, below the cut


def test_the_fixture_lengths_are_what_the_names_say():
    assert len(AT_36) == levelled.LEVEL_TRUNCATION == 36 and len(BELOW) == 34


def _per_level(framework: str, *labels: str) -> Control:
    requirements = [
        Requirement(
            f"R.1 {label}",
            label,
            "A synthetic statement.",
            MATURITY if label[-1].isdigit() else OVERLAY,
        )
        for label in labels
    ]
    return Control("R.1", "R.1", framework, "v1", requirements=requirements)


def _framework_coverage(catalog):
    from policyforge.topics.coverage import _framework_coverage as coverage

    every = sorted(r.requirement_id for c in catalog for r in c.requirements)
    return coverage(
        catalog,
        {"AC-2": {"hitrust-csf": every}},
        owned={"AC-2"},
        relationships={},
        family_links={},
        scopes={},
    )


def test_coverage_names_hitrusts_labels_at_the_cut_width():
    (result,) = _framework_coverage([_per_level(levelled.FRAMEWORK, "Level 1", AT_36, BELOW)])
    assert result.levels_at_cut_width == {AT_36: 1}


def test_another_per_level_framework_is_not_measured_against_hitrusts_cut():
    """The 36-character cut is the HITRUST CSV exporter's, not a property of
    every catalog with levels."""
    (result,) = _framework_coverage([_per_level("Invented Levels", "Level 1", AT_36)])
    assert result.levels_at_cut_width == {}


def test_the_coverage_json_carries_it(tmp_path, monkeypatch):
    """Through the real `coverage --json`, as #13's scoping is reported."""
    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    controls = levelled.build_controls(
        [
            levelled.Record(reference="01.a Invented", level="Level 1", statement="Invented.",
                           mapping="NIST SP 800-53 r5 AC-2"),
            levelled.Record(reference="01.a Invented", level=AT_36, statement="Invented.",
                           mapping="NIST SP 800-53 r5 AC-2"),
        ]
    )  # fmt: skip
    catalog = tmp_path / "levels.json"
    catalog.write_text(json.dumps([dataclasses.asdict(c) for c in controls]), encoding="utf-8")
    topics = tmp_path / "topics.yaml"
    topics.write_text(
        "topics:\n  - name: access\n    owner: team-a\n    nist_controls: [AC-2]\n"
        "    cadence: annual\n    description: d\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("POLICYFORGE_CONFIG", str(config))
    from policyforge.scaffold import bundled_root

    nist = bundled_root().joinpath("frameworks", "nist-800-53-r5", "controls.json")
    result = CliRunner().invoke(
        cli_mod.cli,
        ["coverage", "--topics", str(topics), "--controls", str(nist), "--controls", str(catalog),
         "--json"],
    )  # fmt: skip
    assert result.exit_code in (0, 1), result.output
    (entry,) = [
        f
        for f in json.loads(result.output)["framework_coverage"]
        if f["framework"] == "hitrust-csf"
    ]
    assert entry["levels_at_cut_width"] == {AT_36: 1}


def test_coverage_measures_with_no_hitrust_parser_in_the_tree():
    """No importer to block: the parser is not in this tree at all (#32, part
    3; `test_licensed_parsers_absent.py` holds that), and coverage still
    NAMES the labels."""
    (result,) = _framework_coverage([_per_level(levelled.FRAMEWORK, "Level 1", AT_36)])
    assert result.levels_at_cut_width == {AT_36: 1}

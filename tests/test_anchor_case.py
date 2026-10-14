"""An anchor's case doesn't change what any reader answers (#188, CR-26).

Five readers once compared anchors two ways: `coverage`, `bundle` and
`addresses` verbatim, `drift` and `parameters` upper-cased. So a topic file
writing `ac-3` was unknown to three and owned in two. Two more read anchors
verbatim and weren't in the finding: `satisfies` ("anchored, cited nowhere")
and `synthesize`, which pulled no control at all for a lower-case anchor,
silently, while the topic's other anchors still resolved.

The fix is one rule where anchors are read in, `anchoring.canonical_anchor`:
an id with the shape of an anchorable catalog's ids, in any case, becomes
that catalog's spelling, and anything else is kept as written. Every reader
then compares one spelling. These tests load a lower-case topic file the way
the CLI does (`load_topics`) and require each reader's answer to equal the
answer for the catalog-spelled file, non-empty, so one switched-off rule turns
every reader's test red by name.

Inputs are written by hand from the catalogs, not from the rule's regexes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.cli._common import load_catalogs
from policyforge.topics.anchoring import canonical_anchor
from policyforge.topics.registry import load_topics

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
NIST = FRAMEWORKS / "nist-800-53-r5" / "controls.json"
AI_RMF = FRAMEWORKS / "nist-ai-rmf" / "controls.json"

#: The same registry twice: once as a careless hand writes it, once in the
#: catalogs' spelling. `ac-03` and `164.308(a)(1)` are unknown either way.
_REGISTRY = """\
topics:
  - name: Access
    owner: IAM
    nist_controls: [{access}]
  - name: Odd
    owner: GRC
    nist_controls: [{odd}]
"""
LOWER = {"access": "ac-3, ia-2(1), govern 1", "odd": "ac-03, 164.308(a)(1)"}
CANON = {"access": "AC-3, IA-2(1), Govern 1", "odd": "AC-03, 164.308(a)(1)"}


def _registry(tmp_path: Path, spelling: dict[str, str], name: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(_REGISTRY.format(**spelling), encoding="utf-8")
    return path


@pytest.fixture
def registries(tmp_path):
    return (
        load_topics(_registry(tmp_path, LOWER, "lower")),
        load_topics(_registry(tmp_path, CANON, "canon")),
    )


@pytest.fixture(scope="module")
def catalogs():
    return load_catalogs([NIST, AI_RMF])


def _ids(path: Path) -> list[str]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [r["control_id"] for r in rows] + [
        e["enhancement_id"] for r in rows for e in (r.get("enhancements") or [])
    ]


# ---- the rule ----------------------------------------------------------------


def test_every_id_a_topic_can_anchor_is_already_canonical():
    """The rewrite changes nothing a catalog spells: every 800-53 and AI RMF
    id is a fixed point. Counted, so an emptied population can't pass."""
    nist, ai = _ids(NIST), _ids(AI_RMF)
    assert (len(nist), len(ai)) == (1014, 91)
    assert [i for i in nist + ai if canonical_anchor(i) != i] == []


def test_no_id_in_any_other_bundled_catalog_is_rewritten():
    """Case-meaningful ids elsewhere keep their case: HIPAA's `(a)`, CSF's
    `GV.OC-01`, and every other catalog's ids, measured over all of them."""
    others = [
        p
        for p in sorted(FRAMEWORKS.glob("*/controls.json"))
        if p.parent.name not in {"nist-800-53-r5", "nist-ai-rmf"}
    ]
    assert len(others) >= 10, others
    changed = [(p.parent.name, i) for p in others for i in _ids(p) if canonical_anchor(i) != i]
    assert changed == []


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("ac-3", "AC-3"),
        ("Ac-3", "AC-3"),
        ("AC-3", "AC-3"),
        ("ac-3(1)", "AC-3(1)"),
        ("ia-2(12)", "IA-2(12)"),
        ("govern 1", "Govern 1"),
        ("GOVERN 1.1", "Govern 1.1"),
        ("manage 4.3", "Manage 4.3"),
        # Zero-padding isn't case: 800-53 never pads, so this stays unknown.
        ("ac-03", "AC-03"),
        # Not an anchorable shape: kept exactly, case and all.
        ("164.308(a)(1)", "164.308(a)(1)"),
        ("gv.oc-01", "gv.oc-01"),
        ("GV.OC-01", "GV.OC-01"),
        ("Govern  1", "Govern  1"),
    ],
)
def test_an_anchor_becomes_its_catalogs_spelling_and_nothing_else_moves(written, canonical):
    assert canonical_anchor(written) == canonical


def test_the_registry_reads_anchors_in_their_catalogs_spelling(registries):
    lower, canon = registries
    assert lower[0].nist_controls == ["AC-3", "IA-2(1)", "Govern 1"]
    assert lower[1].nist_controls == ["AC-03", "164.308(a)(1)"]
    assert [t.nist_controls for t in lower] == [t.nist_controls for t in canon]


# ---- every reader gives one answer -------------------------------------------


def test_coverage(registries, catalogs):
    from policyforge.topics.coverage import analyze_coverage

    lower, canon = (analyze_coverage(r, catalogs) for r in registries)
    assert lower.covered == canon.covered
    assert lower.covered["AC-3"] == "Access" and lower.covered["Govern 1.1"] == "Access"
    assert lower.unknown_anchors == canon.unknown_anchors == {"Odd": ["AC-03", "164.308(a)(1)"]}


def test_bundle(registries, catalogs):
    from policyforge.topics.bundles import team_bundle

    lower, canon = (
        [r.requirement_id for r in team_bundle(reg, catalogs, "IAM").requirements]
        for reg in registries
    )
    assert lower == canon and "AC-3" in lower and "IA-2(1)" in lower


def test_addresses(registries, catalogs):
    from policyforge.topics.bundles import requirement_view

    lower, canon = (
        [(c.topic, c.route) for c in requirement_view(reg, catalogs, "AC-3").claims]
        for reg in registries
    )
    assert lower == canon and lower and lower[0][0] == "Access"


def test_drift(registries, catalogs):
    from policyforge.frameworks.drift import CHANGED, ControlChange, assess_impact

    framework = next(c.framework for c in catalogs if c.control_id == "AC-3")
    lower, canon = (
        assess_impact([ControlChange("AC-3", CHANGED)], topics=reg, framework=framework)[
            "AC-3"
        ].topics
        for reg in registries
    )
    assert lower == canon == ["Access"]


def test_parameters(tmp_path):
    from policyforge.cli import cli

    def run(name, spelling):
        topics = _registry(tmp_path, spelling, name)
        result = CliRunner().invoke(
            cli, ["parameters", "--controls", str(NIST), "--topics", str(topics)]
        )
        assert result.exit_code == 0, result.output
        return result.output

    lower, canon = run("lower", LOWER), run("canon", CANON)
    assert lower == canon and "AC-3" in lower


def test_satisfies(tmp_path):
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.satisfies import build_report
    from tests.test_satisfies import FakeDocument, _catalogs

    controls = _catalogs()
    path = tmp_path / "t.yaml"

    def anchored_not_cited(anchors):
        path.write_text(
            f"topics:\n  - name: Access\n    owner: IAM\n    nist_controls: [{anchors}]\n",
            encoding="utf-8",
        )
        documents = [FakeDocument("[NIST 800-53 AC-2]", path="standards/access.md", slug="access")]
        (evidence,) = build_report(
            documents,
            controls=controls,
            crosswalk=build_crosswalk(controls),
            topics=load_topics(path),
        )
        return evidence.anchored_not_cited

    assert anchored_not_cited("ac-2, ir-3") == anchored_not_cited("AC-2, IR-3") == ["IR-3"]


def test_synthesis_pulls_the_same_controls(registries, catalogs):
    from policyforge.synthesis.merge import build_synthesis_topic

    lower, canon = (
        [
            c.control_id
            for c in build_synthesis_topic(
                "Access", reg[0].nist_controls, catalogs, {}, {}
            ).controls
        ]
        for reg in registries
    )
    assert lower == canon and {"AC-3", "IA-2", "Govern 1"} <= set(lower)


def test_synthesize_reads_the_nist_controls_flag_by_the_same_rule(tmp_path, monkeypatch):
    """`--nist-controls` is the other way anchors arrive. A lower-case id there
    must find its control. The provider is replaced by one that stops the run,
    so reaching it PROVES the controls were found; the control case, an id no
    catalog holds, shows "No controls found" is the check that speaks first."""
    import policyforge.cli as cli_mod

    class _FoundControls(Exception):
        """Raised where the provider would be built: past the controls check."""

    def _stop(config):
        raise _FoundControls

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"provider": "local"}})
    monkeypatch.setattr(cli_mod, "get_provider", _stop)
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")

    def run(ids):
        return CliRunner().invoke(
            cli_mod.cli,
            [
                "synthesize",
                "--topic",
                "Access",
                "--nist-controls",
                ids,
                "--controls",
                str(NIST),
                "--crosswalk",
                str(crosswalk),
                "--out-dir",
                str(tmp_path / "out"),
            ],
        )

    missing = run("zz-99")
    assert "No controls found" in missing.output, missing.output
    found = run("ac-3")
    assert isinstance(found.exception, _FoundControls), (found.exception, found.output)


# ---- 800-53 ids read in from another catalog's crosswalk (9b on #212) ---------


def _hitrust(**kwargs):
    from policyforge.ingest.schema import Control

    return Control(
        control_id="01.c",
        title="Privilege Management",
        framework="HITRUST",
        framework_version="11",
        **kwargs,
    )


def test_a_catalogs_lower_case_crosswalk_cell_still_reaches_the_topic():
    """A catalog of the organization's own may write its 800-53 equivalent as
    `ac-3`. drift and parameters used to reach it by upper-casing both sides;
    with one rule at read-in, the crosswalk holds `AC-3` and drift still
    reaches the topic (9b's note on #212, measured: [] without this)."""
    from policyforge.frameworks.drift import CHANGED, ControlChange, assess_impact
    from policyforge.ingest.schema import Control
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.registry import Topic

    nist = Control(
        control_id="AC-3", title="Access", framework="NIST 800-53", framework_version="r5"
    )
    crosswalk = build_crosswalk([nist, _hitrust(source_crosswalk={"NIST 800-53": "ac-3"})])
    assert sorted(crosswalk) == ["AC-3"]
    topic = Topic(name="Access", owner="IAM", nist_controls=["AC-3"])
    reach = assess_impact(
        [ControlChange("01.c", CHANGED)], topics=[topic], framework="HITRUST", crosswalk=crosswalk
    )
    assert reach["01.c"].topics == ["Access"]


def test_a_level_scoped_lower_case_mapping_maps_onto_the_catalog_id():
    """HITRUST's per-level mappings are the crosswalk's other way in."""
    from policyforge.ingest.schema import Requirement
    from policyforge.mapping.crosswalk import build_crosswalk

    level = Requirement(
        requirement_id="01.c L1",
        level="Level 1",
        statement="s",
        mappings={"NIST SP 800-53": ["ac-3"]},
    )
    assert sorted(build_crosswalk([_hitrust(requirements=[level])])) == ["AC-3"]

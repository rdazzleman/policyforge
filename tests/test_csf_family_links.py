"""CSF 2.0's family-level links, read by the reports (#448, 80's ruling).

NIST's OLIR 186 links two CSF ids to WHOLE 800-53 families, not to any
control in them: GV.OC-03 -> PT, and PR.IR-03 -> CP and IR. Written out here
from the issue, not read off the manifest, so a hand edit to `family_links:`
fails against the reports rather than agreeing with them.

- `satisfies`: a document citing a control in the family reaches the CSF id
  by the `family` route, in part, never satisfied, saying why.
- `drift`: both ways (#377's hub rule is symmetric). No generated document
  cites a CSF id, so these are pinned by hand.
- `/coverage`: the links are named beside CSF, and counted in nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.mapping.crosswalk import build_crosswalk, normalize_framework
from policyforge.topics.satisfies import FAMILY_ROUTE, document_evidence, format_report

ROOT = Path(__file__).resolve().parent.parent / "data" / "frameworks"
LINKS = {"GV.OC-03": {"PT"}, "PR.IR-03": {"CP", "IR"}}


@pytest.fixture(scope="module")
def shipped():
    from policyforge.frameworks.registry import declared_family_links
    from policyforge.ingest.schema import load_controls

    controls = [c for p in sorted(ROOT.glob("*/controls.json")) for c in load_controls(p)]
    catalogs: dict[str, set[str]] = {}
    for c in controls:
        ids = catalogs.setdefault(normalize_framework(c.framework), set())
        ids.add(c.control_id)
        ids.update(e.enhancement_id for e in c.enhancements)
    return controls, catalogs, build_crosswalk(controls), declared_family_links(roots=[ROOT])


def test_the_manifest_declares_exactly_the_issues_links(shipped):
    *_, links = shipped
    assert {k: set(v) for k, v in links["nist-csf"].items()} == LINKS


class _Doc:
    def __init__(self, body):
        self.body, self.title, self.relative_path = body, "Doc", "standards/doc.md"


def _reached(shipped, body, *, loaded=True):
    controls, _, crosswalk, links = shipped
    if not loaded:
        controls = [c for c in controls if normalize_framework(c.framework) != "nist-csf"]
        crosswalk = build_crosswalk(controls)
    evidence = document_evidence(
        _Doc(body), controls=controls, crosswalk=crosswalk, family_links=links
    )
    return evidence, {r.requirement_id: r for r in evidence.reached if r.framework == "nist-csf"}


def test_a_control_in_the_family_reaches_the_csf_id_in_part_by_the_family_route(shipped):
    """PT-2 has no control-level link to GV.OC-03 (PT-1 does): only the
    family reaches it."""
    evidence, reached = _reached(shipped, "Consent is recorded. [NIST 800-53 PT-2]")
    item = reached["GV.OC-03"]
    assert (item.route, item.via, item.in_part) == (FAMILY_ROUTE, "PT", True)
    text = format_report([evidence])
    assert "Reached through a family link" in text
    line = next(ln for ln in text.splitlines() if "GV.OC-03" in ln)
    assert "in part" in line and "satisfied" not in line and "whole family" in line


def test_a_control_level_link_is_shown_as_one_and_not_again_as_a_family(shipped):
    """PT-1 is one of GV.OC-03's control-level links: one entry, crosswalked."""
    _, reached = _reached(shipped, "Policy exists. [NIST 800-53 PT-1]")
    assert reached["GV.OC-03"].route == "crosswalked"


def test_no_family_route_when_csf_is_not_loaded(shipped):
    _, reached = _reached(shipped, "Consent is recorded. [NIST 800-53 PT-2]", loaded=False)
    assert "GV.OC-03" not in reached


def test_a_control_outside_every_linked_family_reaches_nothing_by_family(shipped):
    _, reached = _reached(shipped, "Accounts are managed. [NIST 800-53 AC-2]")
    assert all(r.route != FAMILY_ROUTE for r in reached.values())


# ---- drift: both ways (80's ruling) -----------------------------------------


def _docs(tmp_path, **bodies):
    root = tmp_path / "docs" / "standards"
    root.mkdir(parents=True)
    for name, body in bodies.items():
        (root / f"{name}.md").write_text(f"# {name}\n\n{body}\n", encoding="utf-8")
    return tmp_path / "docs"


def _drift(shipped, change, framework, root, *, links=True):
    from policyforge.frameworks.drift import documents_reached

    _, catalogs, crosswalk, family_links = shipped
    return documents_reached(
        {change},
        root,
        framework=framework,
        catalog_ids=catalogs[normalize_framework(framework)],
        crosswalk=crosswalk,
        catalogs=catalogs,
        family_links=family_links if links else None,
    ).get(change, {})


def test_a_change_in_the_family_reaches_a_document_citing_the_csf_id(shipped, tmp_path):
    root = _docs(tmp_path, csf="Context is understood. [NIST CSF 2.0 GV.OC-03]")
    assert "standards/csf.md" in _drift(shipped, "PT-2", "NIST 800-53", root)
    assert "standards/csf.md" not in _drift(shipped, "PT-2", "NIST 800-53", root, links=False)


def test_a_change_to_the_csf_id_reaches_a_document_citing_the_family(shipped, tmp_path):
    root = _docs(tmp_path, pt="Consent is recorded. [NIST 800-53 PT-2]")
    assert "standards/pt.md" in _drift(shipped, "GV.OC-03", "NIST CSF 2.0", root)
    assert "standards/pt.md" not in _drift(shipped, "GV.OC-03", "NIST CSF 2.0", root, links=False)


def test_a_family_link_never_joins_two_800_53_controls_or_the_wrong_family(shipped, tmp_path):
    """#377 stays control-level inside 800-53, and CP/IR belong to PR.IR-03."""
    root = _docs(
        tmp_path,
        pt3="Processing is limited. [NIST 800-53 PT-3]",
        csf_ir="Resilience is designed in. [NIST CSF 2.0 PR.IR-03]",
    )
    reached = _drift(shipped, "PT-2", "NIST 800-53", root)
    assert "standards/pt3.md" not in reached
    assert "standards/csf_ir.md" not in reached


# ---- /coverage: named, never counted -----------------------------------------


def test_coverage_names_the_links_and_counts_nothing_by_them(shipped):
    from policyforge.topics.coverage import _framework_coverage

    controls, _, crosswalk, links = shipped
    csf = [c for c in controls if normalize_framework(c.framework) == "nist-csf"]
    owned = {"PT-2", "IR-4", "CP-2"}
    kwargs = {
        "owned": owned,
        "relationships": {},
        "crosswalk_sources": {"nist-csf": "NIST OLIR 186"},
    }
    (with_links,) = _framework_coverage(csf, crosswalk, family_links=links, **kwargs)
    (without,) = _framework_coverage(csf, crosswalk, family_links={}, **kwargs)
    assert {k: set(v) for k, v in with_links.family_links.items()} == LINKS
    assert (with_links.covered, with_links.partial) == (without.covered, without.partial)


def test_a_family_link_into_anything_but_800_53_is_refused(tmp_path):
    from policyforge.frameworks.registry import declared_family_links

    catalog = tmp_path / "x"
    catalog.mkdir()
    (catalog / "framework.yaml").write_text(
        "id: x\nname: X\nframework_id: x\nfamily_links:\n  relationship: family\n"
        "  framework: nist-800-171\n  links:\n    A-1:\n    - AC\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="family_links must be"):
        declared_family_links(roots=[tmp_path])

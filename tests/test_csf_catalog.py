"""The shipped NIST CSF 2.0 catalog and its OLIR 186 mapping (#408).

Figures are derived from the shipped files and pinned against independent
counts: 6/22/106 is NIST's own statement of the Core (CSWP 29) and agreed
by four instruments on #408; the 742 control links on 108 CSF ids, the
three family links and the one refused target were measured by ba from the
workbook with a second reader (openpyxl, not the parser) on #408.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

from policyforge.ingest import csf
from policyforge.ingest.oscal_loader import CROSSWALK_KEY_800_53

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "frameworks" / "nist-csf-2-0"
NIST_800_53 = ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"


@pytest.fixture(scope="module")
def rows():
    return json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load((CATALOG / "framework.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ids_800_53():
    data = json.loads(NIST_800_53.read_text(encoding="utf-8"))
    return {r["control_id"] for r in data} | {
        e["enhancement_id"] for r in data for e in r["enhancements"]
    }


def _links(rows):
    """{CSF id: [800-53 ids]}, from categories and subcategories alike."""
    found = {}
    for row in rows:
        for node_id, crosswalk in [
            (row["control_id"], row["source_crosswalk"]),
            *((e["enhancement_id"], e["source_crosswalk"]) for e in row["enhancements"]),
        ]:
            if crosswalk:
                found[node_id] = [i.strip() for i in crosswalk[CROSSWALK_KEY_800_53].split(",")]
    return found


# ---- the shipped catalog -------------------------------------------------------------


def test_the_core_is_six_functions_twenty_two_categories_one_hundred_and_six_subcategories(rows):
    assert len({r["family_abbr"] for r in rows}) == 6
    assert len(rows) == 22
    assert sum(len(r["enhancements"]) for r in rows) == 106
    assert {r["framework"] for r in rows} == {"NIST CSF 2.0"}


def test_no_withdrawn_csf_1_1_id_ships(rows):
    """`ID.AM-06` was withdrawn into 2.0's `ID.AM-05`; a withdrawn id in the
    catalog would be a citation CSF 2.0 does not have."""
    ids = {e["enhancement_id"] for r in rows for e in r["enhancements"]}
    assert "ID.AM-06" not in ids and "PR.AC-01" not in ids
    assert "ID.AM-05" in ids and "GV.OC-01" in ids


def test_the_mapping_is_nists_extent_and_every_target_resolves(rows, ids_800_53):
    links = _links(rows)
    assert len(links) == 108
    assert sum(len(v) for v in links.values()) == 742
    unresolved = sorted({i for v in links.values() for i in v} - ids_800_53)
    assert unresolved == [], f"targets that name no shipped 800-53 id: {unresolved}"
    # The two categories NIST links directly; every subcategory is linked.
    assert {k for k in links if "-" not in k} == {"RS.MA", "RC.RP"}


def test_a_family_target_never_enters_the_control_crosswalk(rows, manifest):
    """80's ruling: family links are typed and kept apart BY CONSTRUCTION.
    `PT`, `CP` and `IR` are family abbreviations, and none of them is an
    id in any `source_crosswalk`."""
    links = _links(rows)
    assert not [i for v in links.values() for i in v if "-" not in i]
    family = manifest["family_links"]
    assert family["relationship"] == "family"
    assert family["framework"] == CROSSWALK_KEY_800_53
    assert family["links"] == {"GV.OC-03": ["PT"], "PR.IR-03": ["CP", "IR"]}


def test_the_withdrawn_target_is_refused_not_carried(rows):
    assert "RA-4" not in _links(rows)["DE.AE-06"]


def test_the_manifest_declares_the_mapping_untyped_and_pins_both_files(manifest):
    assert manifest["crosswalk_relationship"] == "source-untyped"
    assert manifest["crosswalk_source"] == "NIST OLIR 186"
    assert manifest["licence"] == "public-domain"
    assert manifest["crosswalk_source_sha256"] == csf.OLIR_186_SHA256
    assert manifest["source_sha256"] == csf.CATALOG_SHA256


def test_the_manifests_family_links_are_the_pinned_workbooks(manifest, ids_800_53):
    """Re-derived from the pinned workbook, not compared with themselves:
    `content_sha256` covers `controls.json` only, so nothing else would notice
    a hand edit to `family_links:` (80, on #449)."""
    _, families, _ = csf.parse_olir_186(OLIR_FIXTURE.read_bytes(), ids_800_53)
    assert manifest["family_links"]["links"] == families


def test_only_csf_declares_a_crosswalk_source():
    from policyforge.frameworks.registry import declared_crosswalk_sources

    sources = declared_crosswalk_sources(roots=[ROOT / "data" / "frameworks"])
    assert sources == {"nist-csf": "NIST OLIR 186"}


def test_only_csf_declares_a_crosswalk_relationship():
    """Adding the value moves no other bundled catalog (80's condition on #408)."""
    from policyforge.frameworks.registry import declared_crosswalk_relationships

    declared = declared_crosswalk_relationships(roots=[ROOT / "data" / "frameworks"])
    assert declared == {"nist-csf": "source-untyped"}


def test_no_other_catalogs_coverage_moves():
    """Every bundled catalog's coverage, with the declarations read, is what
    it was with none; CSF alone moves, from full to partial."""
    from policyforge.crosswalk.overlay import relationships_for
    from policyforge.frameworks.registry import declared_crosswalk_relationships
    from policyforge.ingest.schema import load_controls
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.coverage import analyze_coverage
    from policyforge.topics.registry import load_topics

    frameworks = ROOT / "data" / "frameworks"
    controls = [c for p in sorted(frameworks.glob("*/controls.json")) for c in load_controls(p)]
    nist = [c for c in controls if c.framework == "NIST 800-53"]
    other = [c for c in controls if c.framework != "NIST 800-53"]
    topics = load_topics(ROOT / "config" / "topics.example.yaml")
    crosswalk = build_crosswalk(controls)
    declared = declared_crosswalk_relationships(roots=[frameworks])

    def figures(relationships):
        report = analyze_coverage(
            topics, nist, other_controls=other, crosswalk=crosswalk, relationships=relationships
        )
        return {
            f.framework: (len(f.covered), len(f.partial), len(f.uncovered))
            for f in report.framework_coverage
        }

    before = figures(relationships_for(controls, [], declared={}))
    after = figures(relationships_for(controls, [], declared=declared))
    moved = {k for k in before if before[k] != after[k]}
    assert moved == {"nist-csf"}
    covered, partial, _ = after["nist-csf"]
    assert covered == 0 and partial > 0, "a CSF id read as covered on NIST's untyped link"


# ---- the parser, on hand-written rows ------------------------------------------------


def _workbook(rows) -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Relationships"
    sheet.append(["Focal Document Element", "Rationale", "Reference Document Element"])
    for row in rows:
        sheet.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


CATALOG_IDS = {"AC-2", "AC-2(3)", "IR-4", "CM-7(2)", "SI-4"}


def test_padding_is_removed_from_controls_and_enhancements():
    raw = _workbook(
        [("GV.OC-01", "", "IR-04"), ("GV.OC-01", "", "CM-7(02)"), ("GV.OC-02", "", " AC-02(03) ")]
    )
    links, families, refused = csf.parse_olir_186(raw, CATALOG_IDS)
    assert links == {"GV.OC-01": ["IR-4", "CM-7(2)"], "GV.OC-02": ["AC-2(3)"]}
    assert families == {} and refused == []


def test_a_section_header_row_carries_nothing():
    raw = _workbook([("GV", "", None), ("GV.OC", "", ""), ("GV.OC-01", "", "SI-4")])
    links, _, _ = csf.parse_olir_186(raw, CATALOG_IDS)
    assert links == {"GV.OC-01": ["SI-4"]}


def test_a_family_target_is_a_family_link_not_a_control():
    raw = _workbook([("GV.OC-03", "", "PT"), ("PR.IR-03", "", "CP"), ("PR.IR-03", "", "IR")])
    links, families, _ = csf.parse_olir_186(raw, CATALOG_IDS)
    assert links == {}
    assert families == {"GV.OC-03": ["PT"], "PR.IR-03": ["CP", "IR"]}


def test_a_repeated_link_is_carried_once_in_order():
    raw = _workbook([("GV.OC-01", "", "SI-4"), ("GV.OC-01", "", "AC-2"), ("GV.OC-01", "", "SI-04")])
    links, _, _ = csf.parse_olir_186(raw, CATALOG_IDS)
    assert links == {"GV.OC-01": ["SI-4", "AC-2"]}


def test_the_known_withdrawn_target_is_refused_by_name():
    raw = _workbook([("DE.AE-06", "", "RA-04"), ("DE.AE-06", "", "SI-4")])
    links, _, refused = csf.parse_olir_186(raw, CATALOG_IDS)
    assert links == {"DE.AE-06": ["SI-4"]}
    assert refused == [("DE.AE-06", "RA-4")]


@pytest.mark.parametrize(
    "target",
    ["XX-9", "AC-2 (3)", "ac-2", "AC-2.3", "AC-99(1)", "Control AC-2"],
    ids=["no-such-control", "spaced-enhancement", "lower-case", "dotted", "no-such-enh", "prose"],
)
def test_any_other_unresolved_target_stops_the_run(target):
    """Written from shapes a spreadsheet produces, not from the parser's
    pattern: each is dropped silently by a parser that only skips misses."""
    raw = _workbook([("GV.OC-01", "", target)])
    with pytest.raises(csf.CsfError, match="Refusing rather than dropping"):
        csf.parse_olir_186(raw, CATALOG_IDS)


def test_a_different_file_is_refused():
    with pytest.raises(csf.CsfError, match="not the pinned"):
        csf.require_sha256(b"not the workbook", csf.OLIR_186_SHA256, "The OLIR 186 workbook")


def test_attach_refuses_a_focal_id_the_core_does_not_have():
    from policyforge.ingest.schema import Control

    core = [Control(control_id="GV.OC", title="t", framework="NIST CSF 2.0", framework_version="2")]
    with pytest.raises(csf.CsfError, match=r"ID\.AM-06"):
        csf.attach(core, {"ID.AM-06": ["AC-2"]}, {})


def test_etl_refuses_a_different_file_cleanly_and_writes_nothing(tmp_path):
    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    wrong = tmp_path / "catalog.json"
    wrong.write_bytes(b"{}")
    olir = tmp_path / "olir.xlsx"
    olir.write_bytes(b"not it")
    out = tmp_path / "out" / "controls.json"
    result = CliRunner().invoke(
        cli_mod.cli, ["etl-csf", "--oscal", str(wrong), "--olir", str(olir), "--out", str(out)]
    )
    assert result.exit_code == 1
    assert "not the pinned" in result.output
    assert "Traceback" not in result.output
    assert not out.exists()


# ---- the ETL on the pinned files, on the user's path ---------------------------------

OSCAL_FIXTURE = ROOT / "tests" / "fixtures" / "oscal_csf_2_0.json"
OLIR_FIXTURE = ROOT / "tests" / "fixtures" / "olir_186_csf_2_0_to_800-53r5.xlsx"


def test_etl_on_the_pinned_files_writes_the_shipped_catalog(tmp_path):

    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    out = tmp_path / "nist-csf-2-0" / "controls.json"
    out.parent.mkdir()
    # Without `family_links`, so the comparison below sees what the ETL
    # wrote rather than what the copy already carried.
    start = yaml.safe_load((CATALOG / "framework.yaml").read_text(encoding="utf-8"))
    del start["family_links"]
    (out.parent / "framework.yaml").write_text(yaml.safe_dump(start), encoding="utf-8")
    result = CliRunner().invoke(
        cli_mod.cli,
        ["etl-csf", "--oscal", str(OSCAL_FIXTURE), "--olir", str(OLIR_FIXTURE), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    # CRLF folded first, as the Playbook's test does: a Windows checkout with
    # core.autocrlf=true hands the shipped file over as CRLF; the ETL writes LF.
    shipped_bytes = (CATALOG / "controls.json").read_bytes().replace(b"\r\n", b"\n")
    assert out.read_bytes() == shipped_bytes
    written = yaml.safe_load((out.parent / "framework.yaml").read_text(encoding="utf-8"))
    shipped = yaml.safe_load((CATALOG / "framework.yaml").read_text(encoding="utf-8"))
    assert written["family_links"] == shipped["family_links"]
    assert written["content_sha256"] == shipped["content_sha256"]
    assert "Refused DE.AE-06 -> RA-4" in result.output


@pytest.fixture(scope="module")
def parsed():
    from policyforge.ingest.oscal_loader import NIST_CSF_2_0, parse_oscal_catalog

    catalog = json.loads(OSCAL_FIXTURE.read_bytes())
    controls, withdrawn = parse_oscal_catalog(catalog, dialect=NIST_CSF_2_0)
    assert withdrawn == 91
    return controls


def test_the_extent_guard_accepts_the_pinned_parse(parsed):
    from policyforge.ingest.oscal_loader import require_csf_2_0_extent

    require_csf_2_0_extent(parsed)


@pytest.mark.parametrize("change", ["drop-subcategory", "add-withdrawn", "drop-category"])
def test_the_extent_guard_refuses_a_short_or_long_parse(parsed, change):
    import copy

    from policyforge.ingest.oscal_loader import require_csf_2_0_extent
    from policyforge.ingest.schema import ControlEnhancement

    controls = copy.deepcopy(parsed)
    if change == "drop-subcategory":
        controls[0].enhancements.pop()
    elif change == "add-withdrawn":
        controls[0].enhancements.append(ControlEnhancement("ID.AM-06", "", "", ""))
    else:
        controls.pop()
    with pytest.raises(ValueError, match="Refusing to write it"):
        require_csf_2_0_extent(controls)


def test_the_extent_guard_refuses_ids_that_are_not_csf_shaped(parsed):
    """A dialect reading the wrong field produces the right COUNT of wrong ids."""
    import copy

    from policyforge.ingest.oscal_loader import require_csf_2_0_extent

    controls = copy.deepcopy(parsed)
    controls[0].enhancements[0].enhancement_id = "gv.oc-01_smt"
    with pytest.raises(ValueError, match="not CSF-shaped"):
        require_csf_2_0_extent(controls)


def test_the_readme_table_is_the_catalog(rows):
    """Each function row of the catalog README, against `controls.json`."""
    import re

    text = (CATALOG / "README.md").read_text(encoding="utf-8")
    table = {
        m.group(1).upper(): (int(m.group(2)), int(m.group(3)))
        for m in re.finditer(r"^\| (\w+)\s*\| (\d+)\s*\| (\d+)\s*\|$", text, re.M)
    }
    counted: dict[str, list[int]] = {}
    for row in rows:
        entry = counted.setdefault(row["family"], [0, 0])
        entry[0] += 1
        entry[1] += len(row["enhancements"])
    assert table == {k: tuple(v) for k, v in counted.items()}

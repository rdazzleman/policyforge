"""800-171 rev 3 carries NIST's own links to 800-53 (#259).

NIST's rev 3 OSCAL links all 97 requirements to the 800-53 controls they
derive from -- 157 links -- as `rel="reference"` entries in back-matter, in
the SAME list as 199 literature references (SP, IR, FIPS). The loader read
only `rel="related"`, so the shipped catalog carried none of them.

**The traps, each measured on #259 before this was written:**
- the ids are zero-padded (`AC-02(03)`) and 800-53's are not: read as
  written, 22 of 157 resolve -- a partial crosswalk that looks healthy;
- 43 targets are enhancements, nested under their control in the 800-53
  catalog: keyed on `control_id` alone, 114 resolve -- 73%, and convincing;
- the title's shape is the only thing separating a control from a
  publication, so a title that is neither is refused, not guessed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from policyforge.ingest.oscal_loader import (
    CROSSWALK_KEY_800_53,
    NIST_800_53_REV5,
    NIST_800_171_REV3,
    parse_oscal_catalog,
)

FIXTURES = Path(__file__).parent / "fixtures"
EXCERPT_171 = FIXTURES / "oscal_800-171r3_excerpt.json"
EXCERPT_53 = FIXTURES / "oscal_800-53r5_excerpt.json"


def _excerpt() -> dict:
    return json.loads(EXCERPT_171.read_text(encoding="utf-8"))


def _ids(control) -> list[str]:
    return [i.strip() for i in control.source_crosswalk.get(CROSSWALK_KEY_800_53, "").split(",")]


# -- the parse, on NIST's own text -------------------------------------------------


def test_the_padding_is_normalised_on_the_real_excerpt():
    controls, _ = parse_oscal_catalog(_excerpt(), dialect=NIST_800_171_REV3)
    first = next(c for c in controls if c.control_id == "03.01.01")
    # NIST writes AC-02, AC-02(03), AC-02(05), AC-02(13).
    assert _ids(first) == ["AC-2", "AC-2(3)", "AC-2(5)", "AC-2(13)"]
    assert all(c.source_crosswalk for c in controls), "every requirement links to 800-53"


def test_publications_are_not_mappings():
    controls, _ = parse_oscal_catalog(_excerpt(), dialect=NIST_800_171_REV3)
    for control in controls:
        for identifier in _ids(control):
            assert not identifier.startswith(("SP", "IR", "FIPS")), identifier


def test_800_53s_own_reference_links_are_never_read_as_a_mapping():
    """800-53's reference links are literature. The excerpt has 32 of them,
    so this is a real input, not a constructed one."""
    catalog = json.loads(EXCERPT_53.read_text(encoding="utf-8"))
    references = sum(
        1
        for group in catalog["catalog"]["groups"]
        for control in group.get("controls", [])
        for link in control.get("links", [])
        if link.get("rel") == "reference"
    )
    assert references > 0, "the premise: the 800-53 excerpt carries reference links"
    controls, _ = parse_oscal_catalog(catalog, dialect=NIST_800_53_REV5)
    assert not any(c.source_crosswalk for c in controls)


def _with_title(title: str) -> dict:
    """The excerpt with one requirement's first 800-53 reference retitled."""
    catalog = _excerpt()
    body = catalog["catalog"]
    first = body["groups"][0]["controls"][0]
    href = next(
        link["href"].lstrip("#")
        for link in first["links"]
        if link.get("rel") == "reference"
        and any(
            r["uuid"] == link["href"].lstrip("#") and r.get("title", "").startswith("AC-")
            for r in body["back-matter"]["resources"]
        )
    )
    for resource in body["back-matter"]["resources"]:
        if resource["uuid"] == href:
            resource["title"] = title
    return catalog


@pytest.mark.parametrize(
    "title",
    ["CSF 2.0 PR.AA-01", "Account Management", "AC-2", "", "ac-02"],
    ids=["other-framework", "prose", "unpadded-but-valid", "empty", "lower-case"],
)
def test_a_reference_title_that_is_neither_an_id_nor_a_publication(title):
    """Refused, not guessed -- except the one shape that IS an 800-53 id
    written without padding, which is still an id."""
    catalog = _with_title(title)
    if title == "AC-2":
        controls, _ = parse_oscal_catalog(catalog, dialect=NIST_800_171_REV3)
        assert "AC-2" in _ids(controls[0])
        return
    with pytest.raises(ValueError, match="neither a 800-53 id nor a publication"):
        parse_oscal_catalog(catalog, dialect=NIST_800_171_REV3)


def test_a_reference_to_a_missing_resource_is_refused():
    catalog = _excerpt()
    first = catalog["catalog"]["groups"][0]["controls"][0]
    first["links"].append({"href": "#no-such-resource", "rel": "reference"})
    with pytest.raises(ValueError, match="neither a 800-53 id nor a publication"):
        parse_oscal_catalog(catalog, dialect=NIST_800_171_REV3)


def test_a_repeated_link_is_carried_once():
    catalog = _excerpt()
    first = catalog["catalog"]["groups"][0]["controls"][0]
    first["links"] = first["links"] + copy.deepcopy(first["links"])
    controls, _ = parse_oscal_catalog(catalog, dialect=NIST_800_171_REV3)
    ids = _ids(controls[0])
    assert len(ids) == len(set(ids)) == 4


# -- the shipped catalog: read AND resolved -------------------------------------------


def test_the_shipped_catalog_carries_157_links_that_all_resolve_by_both_routes():
    """157 read is not the claim; 157 RESOLVED is, and by the two routes the
    800-53 catalog offers: 114 control rows and 43 nested enhancements."""
    root = Path(__file__).resolve().parent.parent / "data" / "frameworks"
    rows = json.loads((root / "nist-800-171-r3" / "controls.json").read_text(encoding="utf-8"))
    nist = json.loads((root / "nist-800-53-r5" / "controls.json").read_text(encoding="utf-8"))
    control_ids = {c["control_id"] for c in nist}
    enhancement_ids = {e["enhancement_id"] for c in nist for e in c["enhancements"]}
    links = [
        i.strip()
        for r in rows
        for i in (r["source_crosswalk"].get(CROSSWALK_KEY_800_53) or "").split(",")
        if i.strip()
    ]
    assert len(rows) == 97 and sum(bool(r["source_crosswalk"]) for r in rows) == 97
    assert len(links) == 157
    assert sum(i in control_ids for i in links) == 114
    assert sum(i in enhancement_ids for i in links) == 43
    assert all(i in control_ids | enhancement_ids for i in links)


# -- the ETL refuses what does not resolve --------------------------------------------


def _run_etl(monkeypatch, tmp_path, catalog: dict):
    from click.testing import CliRunner

    import policyforge.ingest.oscal_loader as loader
    from policyforge.cli import cli

    monkeypatch.setattr(loader, "fetch_800_171_catalog", lambda **_: catalog)
    out = tmp_path / "nist-800-171-r3" / "controls.json"
    return CliRunner().invoke(cli, ["etl-800-171", "--out", str(out)]), out


def test_the_etl_writes_the_links_it_resolved(monkeypatch, tmp_path):
    result, out = _run_etl(monkeypatch, tmp_path, _excerpt())
    assert result.exit_code == 0, result.output
    assert "all resolved" in result.output
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written[0]["source_crosswalk"] == {"nist-800-53": "AC-2, AC-2(3), AC-2(5), AC-2(13)"}


@pytest.mark.parametrize("title", ["AC-99", "AC-02(99)"])
def test_the_etl_refuses_an_id_800_53_does_not_have(monkeypatch, tmp_path, title):
    """The must-fail arms: shaped like an id, and not one. Nothing is written."""
    result, out = _run_etl(monkeypatch, tmp_path, _with_title(title))
    assert result.exit_code == 1, result.output
    assert "name no control or enhancement" in result.output
    assert "Traceback" not in result.output
    assert not out.exists()

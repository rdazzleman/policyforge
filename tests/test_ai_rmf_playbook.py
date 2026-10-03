"""The AI RMF Playbook catalog: its pin, its extent, and what it refuses to say.

**The count is checked against two instruments, never against a typed
total** (1d, on #177). `EXPECTED_ACTIONS` is `policyforge-f8`'s instrument B
-- NIST's own rendered DOM -- recorded per subcategory. The test below
re-derives instrument A -- column-0 Markdown bullets in the export -- with
its own rule over the raw fixture, independently of the parser. The parser
must agree with both, subcategory by subcategory.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from policyforge.ingest import ai_rmf_playbook as playbook
from policyforge.ingest.ai_rmf_playbook import (
    EXPECTED_ACTIONS,
    FRAMEWORK,
    SOURCE_SHA256,
    AiRmfPlaybookError,
    parse_playbook,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "airc_ai_rmf_playbook.json"
CATALOG = ROOT / "data" / "frameworks" / "nist-ai-rmf-playbook"
CORE = ROOT / "data" / "frameworks" / "nist-ai-rmf" / "controls.json"


@pytest.fixture(scope="module")
def raw() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def core() -> dict[str, str]:
    """The shipped Core's subcategory id -> outcome text."""
    rows = json.loads(CORE.read_text(encoding="utf-8"))
    return {e["enhancement_id"]: e["description"] for row in rows for e in row["enhancements"]}


@pytest.fixture(scope="module")
def parsed(raw, core):
    return parse_playbook(raw, set(core))


def _subcategory(title: str) -> str:
    function, number = title.split()
    return f"{function.capitalize()} {number}"


# ---- the pin -----------------------------------------------------------------------


def test_the_fixture_is_the_pinned_export_byte_for_byte(raw):
    """If git rewrote line endings (core.autocrlf), this fails here, named,
    rather than as a mystery refusal. `.gitattributes` marks it -text."""
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA256


def test_a_different_export_is_refused_before_it_is_parsed(raw, core):
    """A catalog pinned to a revision refuses to ingest a different one. One
    changed byte -- a single space -- is a different export."""
    changed = raw.replace(b'"GOVERN 1.1"', b'"GOVERN  1.1"', 1)
    assert changed != raw
    with pytest.raises(AiRmfPlaybookError, match="pinned to sha256"):
        parse_playbook(changed, set(core))


def test_the_catalog_s_recorded_pin_is_the_module_s(raw):
    import yaml

    meta = yaml.safe_load((CATALOG / "framework.yaml").read_text(encoding="utf-8"))
    assert meta["source_sha256"] == SOURCE_SHA256
    assert meta["source_url"] == playbook.SOURCE_URL
    assert meta["core"] == "nist-ai-rmf 1.0", "the Core pin is stated separately"


# ---- the extent: two instruments, per subcategory --------------------------------------


def test_the_parse_agrees_with_both_independent_counts(raw, parsed):
    """Instrument A, re-derived here with its own rule over the raw bytes;
    instrument B, f8's DOM count, recorded as `EXPECTED_ACTIONS`; and the
    parser. All three, per subcategory. The total is a sum, never a literal."""
    entries = json.loads(raw.decode("utf-8"))
    instrument_a = {
        _subcategory(e["title"]): sum(
            1 for line in e["section_actions"].split("\n") if re.match(r"^[-*] ", line)
        )
        for e in entries
    }
    by_parser = {c.control_id: len(c.enhancements) for c in parsed}

    assert instrument_a == EXPECTED_ACTIONS, "instrument A no longer agrees with B"
    assert by_parser == EXPECTED_ACTIONS, "the parser disagrees with the pinned count"
    assert sum(by_parser.values()) == sum(instrument_a.values())


def test_the_malformed_duplicate_is_not_an_action(parsed):
    """MANAGE 2.2's `-Establish ...` line (no space after the dash) repeats
    the action above it. NIST's renderer continues that action with it, and
    so does this parser. Counting it as an item gives 460."""
    (manage_22,) = [c for c in parsed if c.control_id == "Manage 2.2"]
    assert len(manage_22.enhancements) == EXPECTED_ACTIONS["Manage 2.2"]
    second = manage_22.enhancements[1].description
    assert second.startswith("Establish mechanisms to capture feedback")
    assert "\n-Establish mechanisms to capture feedback" in second


def test_a_parse_that_counts_the_malformed_line_is_refused_whole(raw, core, monkeypatch):
    """The 460 trap, as a parser change: accept `-X` as an item, and the
    extent pin refuses the whole catalog rather than shipping 460."""
    monkeypatch.setattr(playbook, "_ITEM", re.compile(r"^[-*] ?"))
    with pytest.raises(AiRmfPlaybookError, match="refused whole"):
        parse_playbook(raw, set(core))


def test_nested_items_stay_inside_their_action(parsed):
    (govern_14,) = [c for c in parsed if c.control_id == "Govern 1.4"]
    assert any("\n" in e.description for e in govern_14.enhancements)
    assert len(govern_14.enhancements) == EXPECTED_ACTIONS["Govern 1.4"]


def test_lead_ins_are_the_discussion_not_actions(parsed):
    leads = {c.control_id: c.discussion for c in parsed if c.discussion}
    assert leads == {
        "Govern 1.2": "Organizational AI risk management policies should be designed to:",
        "Govern 3.1": "Organizational management can:",
    }


# ---- every entry serves the Core -----------------------------------------------------


def test_every_entry_serves_a_core_subcategory_and_every_subcategory_has_one(parsed, core):
    assert {c.control_id for c in parsed} == set(core)


def _repinned(raw: bytes, monkeypatch, new: bytes) -> bytes:
    """`new`, with the source pin moved to it, so a guard BEHIND the pin can
    be reached. The pin itself is tested above."""
    monkeypatch.setattr(playbook, "SOURCE_SHA256", hashlib.sha256(new).hexdigest())
    return new


def test_an_entry_for_a_subcategory_the_core_lacks_is_refused(raw, core, monkeypatch):
    changed = _repinned(raw, monkeypatch, raw.replace(b'"GOVERN 6.2"', b'"GOVERN 9.9"', 1))
    with pytest.raises(AiRmfPlaybookError, match="refused, not filed"):
        parse_playbook(changed, set(core))


def test_a_core_subcategory_with_no_entry_is_refused(raw, core):
    with pytest.raises(AiRmfPlaybookError, match="no Playbook entry"):
        parse_playbook(raw, set(core) | {"Govern 7.1"})


# ---- what it does not say -------------------------------------------------------------


def test_no_outcome_wording_is_carried(raw, parsed):
    """80's ruling on #177: the export's `description` is its restatement of
    each outcome, and none of it may appear in this catalog. Titles are
    PolicyForge's labels; the outcome is read from `nist-ai-rmf`."""
    descriptions = {
        _subcategory(e["title"]): " ".join(e["description"].split())
        for e in json.loads(raw.decode("utf-8"))
    }
    for control in parsed:
        assert control.title == f"Suggested actions for {control.control_id.upper()}"
        carried = " ".join(
            [control.title, control.discussion, *(e.description for e in control.enhancements)]
        )
        assert descriptions[control.control_id] not in " ".join(carried.split()), (
            f"{control.control_id} carries the export's outcome wording"
        )


def _typography(text: str) -> str:
    """Curly quotes and en/em dashes folded to ASCII -- by code point, so no
    literal typographic character sits in this file to be misread."""
    folds = {0x2019: "'", 0x2018: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-"}
    for code, plain in folds.items():
        text = text.replace(chr(code), plain)
    return " ".join(text.split())


def test_the_observed_divergence_from_the_core_is_what_the_readme_says(raw, core):
    """Pinned so a re-published export that changes it is noticed, and so the
    README's figures cannot drift from the data. Two figures, because 9b
    measured on #304 that the first count (38) folded quotes and dashes but
    not case: 30 subcategories word the outcome differently from AI RMF 1.0,
    and 8 more differ only in capitalisation. Both are derived here."""
    entries = json.loads(raw.decode("utf-8"))

    def differs(entry, fold) -> bool:
        ours = fold(_typography(entry["description"]))
        return ours != fold(_typography(core[_subcategory(entry["title"])]))

    differ = sorted(_subcategory(e["title"]) for e in entries if differs(e, str.casefold))
    case_only = sorted(
        _subcategory(e["title"])
        for e in entries
        if differs(e, str) and not differs(e, str.casefold)
    )
    readme = " ".join((CATALOG / "README.md").read_text(encoding="utf-8").split())
    assert f"in {len(differ)} of the {len(entries)}" in readme, len(differ)
    assert f"in {len(case_only)} more only in capitalisation" in readme, len(case_only)
    for example in ("Govern 1.2", "Govern 1.3", "Govern 4.1"):
        assert example in differ, f"the README's example {example} does not differ"


# ---- keys, citations, README --------------------------------------------------------------


def test_the_playbook_has_its_own_key_and_the_core_keeps_its_own():
    """80's ruling on #177: the Playbook's needle must not invert the
    collision it fixes -- every Core spelling still keys to the Core."""
    from policyforge.mapping.crosswalk import normalize_framework

    assert normalize_framework(FRAMEWORK) == "nist-ai-rmf-playbook"
    assert normalize_framework("AI RMF Playbook") == "nist-ai-rmf-playbook"
    for core_name in (
        "NIST AI RMF",
        "AI RMF",
        "NIST AI Risk Management Framework",
        "NIST AI 100-1",
    ):
        assert normalize_framework(core_name) == "nist-ai-rmf", core_name


def test_an_action_citation_resolves_to_the_playbook_not_the_core(parsed, core):
    """Through `parse_citations`, the path a document's citations take, and
    against EVERY declared catalog name, where the risk is: "NIST AI RMF" is
    a prefix of "NIST AI RMF Playbook", and a shorter match would file the
    action under the Core with an id the Core does not have. (`split_citation`
    alone takes names in the order given; `parse_citations` orders them
    longest first, which is what this depends on.)"""
    from policyforge.content.tags import source_tags
    from policyforge.mapping.crosswalk import normalize_framework
    from policyforge.topics.satisfies import parse_citations

    names = set()
    for directory in sorted((ROOT / "data" / "frameworks").iterdir()):
        controls_json = directory / "controls.json"
        if controls_json.exists():
            names.add(json.loads(controls_json.read_text(encoding="utf-8"))[0]["framework"])
    assert {FRAMEWORK, "NIST AI RMF"} <= names, "both catalogs must be in the population"
    ids = {
        normalize_framework(FRAMEWORK): {e.enhancement_id for c in parsed for e in c.enhancements},
        normalize_framework("NIST AI RMF"): set(core),
    }
    tag = "[NIST AI RMF Playbook Govern 1.1 Action 3]"
    assert source_tags(tag) == [tag]
    found = parse_citations(f"- Keep an inventory. {tag}", sorted(names), ids)
    assert [(f, r, q) for f, r, q, _ in found] == [(FRAMEWORK, "Govern 1.1 Action 3", "")]


def test_the_readme_says_voluntary_first_and_that_generation_does_not_enforce_it():
    readme = (CATALOG / "README.md").read_text(encoding="utf-8")
    first = readme.split("\n\n")[1]
    assert "voluntary" in first and "never say NIST requires" in " ".join(first.split())
    assert "#300" in readme, "the README must say generation does not yet enforce 'suggests'"
    assert "#301" in readme


def test_the_readme_s_counts_are_the_catalog_s(parsed):
    readme = " ".join((CATALOG / "README.md").read_text(encoding="utf-8").split())
    actions = sum(len(c.enhancements) for c in parsed)
    assert f"{len(parsed)} entries, one per Core subcategory" in readme
    assert f"{actions} suggested actions" in readme


def test_the_shipped_catalog_is_the_parse_of_the_pinned_export(parsed):
    import dataclasses

    shipped = json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))
    assert shipped == [dataclasses.asdict(c) for c in parsed]


# ---- the ETL, on the user's path ---------------------------------------------------------------


def test_etl_refuses_a_different_export_with_a_clean_error(tmp_path, raw):
    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    changed = tmp_path / "playbook.json"
    changed.write_bytes(raw.replace(b'"GOVERN 1.1"', b'"GOVERN  1.1"', 1))
    out = tmp_path / "out" / "controls.json"
    result = CliRunner().invoke(
        cli_mod.cli, ["etl-ai-rmf-playbook", "--json", str(changed), "--out", str(out)]
    )
    assert result.exit_code == 1, result.output
    assert "Error: the Playbook export is sha256:" in result.output
    assert "Traceback" not in result.output
    assert not out.exists(), "a refused export wrote a catalog"


def test_etl_on_the_pinned_export_writes_the_shipped_catalog(tmp_path):
    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    out = tmp_path / "controls.json"
    result = CliRunner().invoke(
        cli_mod.cli, ["etl-ai-rmf-playbook", "--json", str(FIXTURE), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    # CRLF folded first, as `provenance.content_digest` does: a Windows
    # checkout with core.autocrlf=true hands the shipped file over as CRLF,
    # and the ETL writes LF on every platform.
    shipped = (CATALOG / "controls.json").read_bytes().replace(b"\r\n", b"\n")
    assert out.read_bytes() == shipped

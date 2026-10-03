"""HIPAA Subparts D and E: the Breach Notification and Privacy Rules (#409).

Figures are ba's measurements on #409, from eCFR at 2026-09-17: 7 and 18
sections carrying 31 and 863 paragraphs once Definitions are excluded; the
renderer's words equal the XML's in all 25 sections; and the text *Purl v.
HHS* vacated is 45 units the rule added plus 14 it revised, agreed by the
eCFR diff and the rule's amendatory instructions (FR 2024-08503).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from policyforge.ingest import hipaa_privacy as hp

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "ecfr_164_subparts_d_e"
FRAMEWORKS = ROOT / "data" / "frameworks"


def _read(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8")


@pytest.fixture(scope="module")
def pin_e():
    return hp.sections(_read("renderer-E.html"), 500, 535)


@pytest.fixture(scope="module")
def pre_e():
    return hp.sections(_read(f"renderer-E-{hp.PRE_RULE_DATE}.html"), 500, 535)


@pytest.fixture(scope="module")
def privacy():
    return json.loads((FRAMEWORKS / hp.PRIVACY_DIR / "controls.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(
        (FRAMEWORKS / hp.PRIVACY_DIR / "framework.yaml").read_text(encoding="utf-8")
    )


# ---- the ETL on the pinned text, on the user's path ---------------------------------


def test_etl_on_the_pinned_text_writes_both_shipped_catalogs(tmp_path):
    """Both `controls.json`, and the Privacy Rule's `vacated:` block, are what
    the ETL writes from the fixtures. The block is removed from the copied
    manifest first, so the comparison sees what the ETL wrote."""
    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    for directory in (hp.BREACH_DIR, hp.PRIVACY_DIR):
        start = yaml.safe_load((FRAMEWORKS / directory / "framework.yaml").read_text("utf-8"))
        start.pop("vacated", None)
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "framework.yaml").write_text(yaml.safe_dump(start), "utf-8")
    result = CliRunner().invoke(
        cli_mod.cli,
        ["etl-hipaa-privacy", "--saved", str(FIXTURES), "--frameworks", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    for directory in (hp.BREACH_DIR, hp.PRIVACY_DIR):
        shipped = (FRAMEWORKS / directory / "controls.json").read_bytes().replace(b"\r\n", b"\n")
        assert (tmp_path / directory / "controls.json").read_bytes() == shipped, directory
    written = yaml.safe_load((tmp_path / hp.PRIVACY_DIR / "framework.yaml").read_text("utf-8"))
    shipped = yaml.safe_load((FRAMEWORKS / hp.PRIVACY_DIR / "framework.yaml").read_text("utf-8"))
    assert written["vacated"] == shipped["vacated"]
    assert "Marked 45 vacated and 14 revised-and-vacated" in result.output


def test_the_pre_rule_source_is_recorded_by_its_hash(manifest):
    raw = (FIXTURES / f"renderer-E-{hp.PRE_RULE_DATE}.html").read_bytes()
    source = manifest["vacated"]["pre_rule_source"]
    assert source["sha256"] == hashlib.sha256(raw).hexdigest()
    assert hp.PRE_RULE_DATE in source["url"]


# ---- extent, and the two instruments ----------------------------------------------


def test_each_catalog_is_its_pinned_extent():
    for directory, extent in (
        (hp.BREACH_DIR, hp.BREACH_EXTENT),
        (hp.PRIVACY_DIR, hp.PRIVACY_EXTENT),
    ):
        rows = json.loads((FRAMEWORKS / directory / "controls.json").read_text(encoding="utf-8"))
        assert (len(rows), sum(len(r["enhancements"]) for r in rows)) == extent, directory


@pytest.mark.parametrize("change", ["short", "long"])
def test_the_extent_guard_refuses_both_directions(change):
    from policyforge.ingest.schema import ControlEnhancement

    controls = hp.to_controls(
        hp.without_definitions(hp.sections(_read("renderer-D.html"), 400, 414)), "x", "x"
    )
    with_paragraphs = next(c for c in controls if c.enhancements)
    if change == "short":
        with_paragraphs.enhancements.pop()
    else:
        with_paragraphs.enhancements.append(ControlEnhancement("164.400(z)", "", "", "text"))
    with pytest.raises(hp.HipaaPrivacyError, match="not the pinned"):
        hp.require_extent(controls, hp.BREACH_EXTENT, "D")


def test_the_renderer_and_the_xml_agree_on_every_section():
    for letter, first, last in (("D", 400, 414), ("E", 500, 535)):
        found = hp.sections(_read(f"renderer-{letter}.html"), first, last)
        hp.require_text_agrees(
            found, hp.xml_section_texts(_read(f"ecfr-{letter}.xml"), first, last)
        )


@pytest.mark.parametrize(
    ("label", "old", "new"),
    [
        (
            "one word",
            "A covered entity shall, following the discovery",
            "A covered entity may, following the discovery",
        ),
        ("a dropped paragraph", r'<div id="p-164\.412\(b\)">.*?</div>', ""),
    ],
)
def test_the_text_guard_catches_a_renderer_that_differs_from_the_xml(label, old, new):
    """The renderer also carries a JSON copy of the text in a `<script>`
    block after the body, so each arm changes the FIRST occurrence, the one in
    the body, and proves the section's text changed before asking the guard."""
    import re

    html = _read("renderer-D.html")
    changed_html = (
        re.sub(old, new, html, count=1, flags=re.S)
        if label == "a dropped paragraph"
        else html.replace(old, new, 1)
    )
    assert changed_html != html, label
    before = {s.sid: hp.section_text(s) for s in hp.sections(html, 400, 414)}
    changed = hp.sections(changed_html, 400, 414)
    assert [s.sid for s in changed if hp.section_text(s) != before[s.sid]], (
        "the arm changed no section's text"
    )
    with pytest.raises(hp.HipaaPrivacyError, match="disagree"):
        hp.require_text_agrees(changed, hp.xml_section_texts(_read("ecfr-D.xml"), 400, 414))


def test_definitions_are_not_requirements():
    for directory in (hp.BREACH_DIR, hp.PRIVACY_DIR):
        rows = json.loads((FRAMEWORKS / directory / "controls.json").read_text(encoding="utf-8"))
        assert not {"164.402", "164.501"} & {r["control_id"] for r in rows}
        ids = [e["enhancement_id"] for r in rows for e in r["enhancements"]]
        assert not [i for i in ids if "%20" in i or "(Breach)" in i], "a term-keyed id was emitted"


def test_a_sections_opening_condition_is_its_statement():
    rows = json.loads((FRAMEWORKS / hp.BREACH_DIR / "controls.json").read_text(encoding="utf-8"))
    statement = next(r for r in rows if r["control_id"] == "164.412")["control_statement"]
    assert statement.startswith("If a law enforcement official states")


def test_ecfrs_id_less_paragraphs_stay_where_ecfr_puts_them():
    rows = json.loads((FRAMEWORKS / hp.BREACH_DIR / "controls.json").read_text(encoding="utf-8"))
    by_id = {e["enhancement_id"]: e for r in rows for e in r["enhancements"]}
    assert "164.404(d)(1)(ii)" not in by_id
    assert (
        "If the covered entity knows the individual is deceased"
        in by_id["164.404(d)(1)"]["description"]
    )


# ---- the vacated set, by the second instrument ----------------------------------------


def test_the_vacated_set_is_what_the_ecfr_diff_finds(pin_e, pre_e, manifest):
    """Instrument 1, independent of `ADDED`/`REVISED` (instrument 2, the
    rule's amendatory instructions): every paragraph of the five sections
    the rule touched whose id and text are not both in eCFR's pre-rule text,
    less what the judgment keeps (§164.520 except (b)(1)(ii)(F)-(H)). It
    must be exactly the manifest's vacated and revised ids."""

    def pairs(found):
        out = {}
        for s in found:
            statement = " ".join(p.text for p in s.paragraphs if not p.pid and not p.term)
            if statement:
                out[s.sid] = statement
            for p in s.paragraphs:
                if p.pid and not p.term:
                    out[p.pid] = p.text
        return out

    before, after = pairs(pre_e), pairs(pin_e)
    touched = ("164.502", "164.509", "164.512", "164.520", "164.535")
    changed = {
        i for i, text in after.items() if i.split("(")[0] in touched and before.get(i) != text
    }
    judgment_keeps = {
        i
        for i in changed
        if i.startswith("164.520")
        and not i.startswith(
            ("164.520(b)(1)(ii)(F)", "164.520(b)(1)(ii)(G)", "164.520(b)(1)(ii)(H)")
        )
    }
    by_diff = changed - judgment_keeps
    vacated = manifest["vacated"]
    declared = set(vacated["added"]) | {i for r in vacated["revised"].values() for i in r["ids"]}
    # The diff pairs by id, so a paragraph the rule only MOVED (same text, a
    # new id) is "changed" here too; §164.502(g)(5)(i)(B) is the one, inside
    # the revised (g)(5), and it is in the manifest for that reason.
    assert by_diff == declared, (sorted(by_diff - declared), sorted(declared - by_diff))
    assert (len(vacated["added"]), len(declared) - len(vacated["added"])) == (45, 14)
    added_under = sorted(i for r in vacated["revised"].values() for i in r["added_ids"])
    assert added_under == sorted(
        i
        for i in declared
        if i not in {p.pid for s in pre_e for p in s.paragraphs if p.pid}
        and i not in vacated["added"]
        and "(" in i
    ), "the rule-added ids under a revised root are the ones the pre-rule text lacks"
    assert len(added_under) == 5


def test_a_re_ingest_where_ecfr_removed_the_vacated_text_refuses(pin_e, pre_e):
    without_509 = [s for s in pin_e if s.sid != "164.509"]
    with pytest.raises(hp.HipaaPrivacyError, match=r"164\.509"):
        hp.vacated_status(without_509, pre_e, pre_rule_sha256="x")


def test_a_changed_vacated_extent_refuses(pin_e, pre_e, monkeypatch):
    monkeypatch.setattr(hp, "EXTENT_VACATED", 44)
    with pytest.raises(hp.HipaaPrivacyError, match="not the pinned 44"):
        hp.vacated_status(pin_e, pre_e, pre_rule_sha256="x")


# ---- generation, crosswalk propose and check read it ------------------------------------


@pytest.fixture(scope="module")
def status():
    from policyforge.frameworks.vacated import declared_vacated

    found = declared_vacated(roots=[FRAMEWORKS])
    assert set(found) == {"hipaa-privacy"}
    return found["hipaa-privacy"]


def _control(control_id):
    from policyforge.ingest.schema import load_controls

    controls = load_controls(FRAMEWORKS / hp.PRIVACY_DIR / "controls.json")
    return next(c for c in controls if c.control_id == control_id)


def test_generation_never_sees_vacated_text(status):
    from policyforge.frameworks.vacated import for_generation

    assert for_generation(_control("164.509"), status) is None
    assert for_generation(_control("164.535"), status) is None
    view = for_generation(_control("164.502"), status)
    ids = [e.enhancement_id for e in view.enhancements]
    assert not [i for i in ids if i.startswith("164.502(a)(5)(iii)")]
    assert not [i for i in ids if i.startswith("164.502(g)(5)(")], "revised children leaked"
    (g5,) = [e for e in view.enhancements if e.enhancement_id == "164.502(g)(5)"]
    assert g5.title.startswith("Pre-rule wording")
    assert g5.description == status.pre_rule["164.502(g)(5)"][1]
    assert "164.502(a)(5)(i)" in ids, "text that binds as printed was dropped"
    raw = _control("164.502")
    assert any(e.enhancement_id.startswith("164.502(a)(5)(iii)") for e in raw.enhancements), (
        "the loaded catalog must keep eCFR's text; only the generation view drops it"
    )


def test_a_revised_opening_sentence_is_generated_from_the_pre_rule_wording(status):
    from policyforge.frameworks.vacated import for_generation

    view = for_generation(_control("164.512"), status)
    assert view.control_statement == status.pre_rule["164.512"][1]
    assert "164.509" not in view.control_statement
    assert "164.512(c)(1)" in [e.enhancement_id for e in view.enhancements]
    assert "164.512(c)(3)" not in [e.enhancement_id for e in view.enhancements]


def test_synthesis_reads_the_generation_view(status):
    from policyforge.ingest.schema import load_controls
    from policyforge.synthesis.merge import build_synthesis_topic

    controls = load_controls(FRAMEWORKS / hp.PRIVACY_DIR / "controls.json")
    crosswalk = {"AC-2": {"hipaa-privacy": ["164.509(a)(1)", "164.502(b)(2)(i)"]}}
    topic = build_synthesis_topic(
        "t", ["AC-2"], controls, crosswalk, vacated={"hipaa-privacy": status}
    )
    assert [c.control_id for c in topic.controls] == ["164.502"]
    ids = [e.enhancement_id for e in topic.controls[0].enhancements]
    assert not [i for i in ids if i.startswith("164.502(a)(5)(iii)")]


def test_crosswalk_propose_does_not_ask_about_vacated_text(status):
    from policyforge.crosswalk.propose import requirements_of
    from policyforge.ingest.schema import load_controls

    controls = load_controls(FRAMEWORKS / hp.PRIVACY_DIR / "controls.json")
    ids = {
        r.requirement_id
        for r in requirements_of(controls, "HIPAA Privacy Rule", vacated={"hipaa-privacy": status})
    }
    assert not [i for i in ids if i.startswith(("164.509", "164.535", "164.502(a)(5)(iii)"))]
    assert "164.502(b)(2)(i)" in ids
    unfiltered = {
        r.requirement_id for r in requirements_of(controls, "HIPAA Privacy Rule", vacated={})
    }
    assert "164.509(a)(1)" in unfiltered, (
        "the arm: without the status the vacated text is asked about"
    )


def test_check_warns_on_vacated_citations_only(status, tmp_path):
    """80, on #409: the 45 `vacated` warn, and so does a revised root's own
    new paragraph (§164.502(g)(5)(i)(A)(1), which the pre-rule text does not
    have), pointed at the root. Any other `vacated-revision` citation
    (§164.502(g)(5)(i)) is valid: the paragraph binds in its pre-rule wording."""
    from policyforge.content.check import _check_vacated_citations
    from policyforge.content.tree import load_content_tree

    (tmp_path / "standards").mkdir()
    lines = [
        "Obtain an attestation. [HIPAA Privacy Rule 164.509(a)(1)]",
        "Follow the representative rule. [HIPAA Privacy Rule 164.502(g)(5)(i)]",
        "Follow the abuse rule. [HIPAA Privacy Rule 164.502(g)(5)(i)(A)(1)]",
        "Note severability. [HIPAA Privacy Rule 164.535]",
        "Disclose as permitted. [HIPAA Privacy Rule 164.512(c)(1)]",
        "Follow the permitted uses. [HIPAA Privacy Rule 164.512]",
        "Notify individuals. [HIPAA Breach Notification Rule 164.404(a)(1)]",
        "Protect the records. [HIPAA Security Rule 164.308(a)(1)(i)]",
    ]
    body = "---\ntitle: P\ntier: standard\n---\n\n# P\n\n" + "\n\n".join(lines) + "\n"
    (tmp_path / "standards" / "p.md").write_text(body, encoding="utf-8")
    documents, _ = load_content_tree(tmp_path)
    findings = _check_vacated_citations(documents, vacated={"hipaa-privacy": status})
    flagged = sorted(f.message.split(" (")[0].split("cites ")[1] for f in findings)
    assert flagged == [
        "HIPAA Privacy Rule 164.502(g)(5)(i)(A)(1)",
        "HIPAA Privacy Rule 164.509(a)(1)",
        "HIPAA Privacy Rule 164.535",
    ]
    (child,) = [f for f in findings if "(g)(5)(i)(A)(1)" in f.message]
    assert "the binding text is 164.502(g)(5)'s pre-rule wording" in child.message
    assert all("Purl v. HHS" in f.message and f.severity == "warning" for f in findings)


# ---- keys, anchoring, reach --------------------------------------------------------------


def test_the_catalogs_key_apart_from_the_security_rule():
    from policyforge.mapping.crosswalk import normalize_framework

    assert normalize_framework("HIPAA Privacy Rule") == "hipaa-privacy"
    assert normalize_framework("HIPAA Breach Notification Rule") == "hipaa-breach"
    assert normalize_framework("HIPAA Security Rule") == "hipaa"


def test_a_change_reaches_a_document_citing_another_paragraph_of_the_section():
    """`family: section` (80's ruling 3): the unit the regulation names. Read
    through `families_for`, the rule `drift` and `satisfies` use (#423), so
    a change to 164.502(b)(2)(i) and a citation of 164.502(a)(1) meet, and
    an eCFR-id-less paragraph's citation stands for its section."""
    from policyforge.ingest.schema import load_controls
    from policyforge.topics.anchoring import families_for, regulatory_families

    controls = [
        c
        for d in (hp.BREACH_DIR, hp.PRIVACY_DIR)
        for c in load_controls(FRAMEWORKS / d / "controls.json")
    ]
    families = families_for(controls)
    privacy, breach = families["hipaa-privacy"], families["hipaa-breach"]
    assert privacy["164.502(a)(1)"] == privacy["164.502(b)(2)(i)"]
    assert privacy["164.502(a)(1)"] != privacy["164.506(a)"]
    assert regulatory_families("164.404(d)(1)(ii)", breach) == {breach["164.404(d)(1)"]}
    assert regulatory_families("164.512(c)", privacy) == {privacy["164.512(c)(1)"]}


def test_the_json_round_trip_is_the_schema():
    from policyforge.ingest.schema import load_controls

    for directory in (hp.BREACH_DIR, hp.PRIVACY_DIR):
        path = FRAMEWORKS / directory / "controls.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        assert [dataclasses.asdict(c) for c in load_controls(path)] == rows

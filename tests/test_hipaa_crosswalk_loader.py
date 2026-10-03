"""ingest/hipaa_crosswalk_loader.py tests, against the real CPRT export in
tests/fixtures/cprt_hipaa_to_800-53r5.json (verbatim responses from NIST's
Cybersecurity and Privacy Reference Tool API, not synthetic data) and the
real eCFR-derived HIPAA data in data/frameworks/hipaa-security-rule/.

Expected values here were hand-verified against that fixture's contents.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from policyforge.mapping.crosswalk import NIST_ANCHOR

FIXTURE = Path(__file__).parent / "fixtures" / "cprt_hipaa_to_800-53r5.json"
HIPAA_DATA = (
    Path(__file__).parent.parent / "data" / "frameworks" / "hipaa-security-rule" / "controls.json"
)


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _mapping() -> dict[str, list[str]]:
    from policyforge.ingest.hipaa_crosswalk_loader import parse_cprt_crosswalk

    return parse_cprt_crosswalk(_payload())


def _controls():
    from policyforge.ingest.schema import load_controls

    return load_controls(HIPAA_DATA)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


# Aliases where CPRT's quoted requirement text and the current CFR text have
# genuinely drifted, with the specific reason and a floor low enough to pass
# today but high enough that further drift still fails. Kept explicit and
# per-citation rather than by relaxing the global threshold, so this stays a
# real check for the other 13 aliases.
KNOWN_TEXT_DRIFT = {
    # CPRT quotes the pre-Omnibus wording: "The contract or other arrangement
    # between the covered entity and its business associate required by
    # § 164.308(b) ...". The current CFR reads "The contract or other
    # arrangement required by § 164.308(b)(3) ...". Same requirement, and the
    # § 164.308(b) -> (b)(3) renumbering is the same one behind the
    # "164.308(b)(4)" -> "164.308(b)(3)" alias.
    "164.314(a)": 0.80,
}


# --------------------------------------------------------------------------
# Parsing the CPRT export
# --------------------------------------------------------------------------


def test_parses_every_crosswalk_row_from_the_fixture():
    mapping = _mapping()
    # 279 OLIR entries across 68 distinct HIPAA citations, as published.
    assert len(mapping) == 68
    assert sum(len(v) for v in mapping.values()) == 279


def test_normalizes_zero_padded_nist_control_ids():
    """CPRT writes "AC-01"/"CP-02(08)"; the rest of the pipeline (and NIST's
    own SP 800-53 text) uses "AC-1"/"CP-2(8)"."""
    from policyforge.ingest.hipaa_crosswalk_loader import _normalize_nist_id

    assert _normalize_nist_id("AC-01") == "AC-1"
    assert _normalize_nist_id("AC-11") == "AC-11"
    assert _normalize_nist_id("CP-02(08)") == "CP-2(8)"
    assert _normalize_nist_id("RA-02(01)") == "RA-2(1)"
    # Anything unrecognizable is refused rather than guessed at.
    assert _normalize_nist_id("Relationships") is None
    assert _normalize_nist_id("") is None


def test_known_mappings_match_the_published_crosswalk():
    """Hand-verified against the fixture: these are semantically obvious
    pairings, so a regression in the join or normalization shows up here."""
    mapping = _mapping()
    assert mapping["164.308(a)(1)(ii)(A)"] == ["RA-2", "RA-3"]  # Risk analysis
    assert mapping["164.308(a)(1)(ii)(C)"] == ["PS-8"]  # Sanction policy
    assert mapping["164.312(a)(2)(iii)"] == ["AC-12"]  # Automatic logoff
    assert mapping["164.312(a)(2)(iv)"] == ["SC-13"]  # Encryption and decryption
    assert mapping["164.310(d)(2)(i)"] == ["MP-6"]  # Disposal


def test_rejects_swapped_focal_and_reference_documents():
    """If CPRT ever flips which document is focal, every HIPAA citation would
    become an SP 800-53 ID and the whole crosswalk would invert. That must
    fail loudly rather than produce a confident, wrong mapping."""
    from policyforge.ingest.hipaa_crosswalk_loader import parse_cprt_crosswalk

    payload = _payload()
    payload["fde"], payload["rde"] = payload["rde"], payload["fde"]
    for element in payload["fde"]["response"]["elements"]:
        element["elementIdentifier"] = "FDE-" + element["elementIdentifier"][4:]
    for element in payload["rde"]["response"]["elements"]:
        element["elementIdentifier"] = "RDE-" + element["elementIdentifier"][4:]

    try:
        parse_cprt_crosswalk(payload)
    except ValueError as exc:
        assert "not a 45 CFR 164 citation" in str(exc)
    else:
        raise AssertionError("expected ValueError when focal/reference roles are swapped")


def test_rejects_misaligned_element_sets():
    """The focal/reference join is by identifier suffix; if the sets stop
    lining up, the pairing is unsafe and parsing must stop."""
    from policyforge.ingest.hipaa_crosswalk_loader import parse_cprt_crosswalk

    payload = _payload()
    payload["rde"]["response"]["elements"].pop()
    try:
        parse_cprt_crosswalk(payload)
    except ValueError as exc:
        assert "do not line up" in str(exc)
    else:
        raise AssertionError("expected ValueError when FDE/RDE/OE sets disagree")


# --------------------------------------------------------------------------
# Reconciling CPRT citations against the eCFR-derived data
# --------------------------------------------------------------------------


def test_citation_aliases_are_text_equivalent_in_both_sources():
    """The heart of this loader's trustworthiness.

    Each `CITATION_ALIASES` entry claims a CPRT citation and an eCFR citation
    describe the *same* requirement. That claim is checked here against both
    sources' actual requirement text, so a CFR renumbering or a CPRT revision
    breaks the suite instead of silently mis-mapping a control.
    """
    from policyforge.ingest.hipaa_crosswalk_loader import CITATION_ALIASES

    cprt_text: dict[str, str] = {}
    for element in _payload()["rde"]["response"]["elements"]:
        cprt_text.setdefault(element["title"].strip(), element["text"].strip())

    ecfr_text = {}
    for control in _controls():
        ecfr_text[control.control_id] = control.control_statement
        for enhancement in control.enhancements:
            ecfr_text[enhancement.enhancement_id] = enhancement.description

    # CPRT formats each requirement as "Title (R|A): body"; compare bodies.
    rolled_up: dict[str, list[str]] = {}
    for cprt_citation, ecfr_citation in CITATION_ALIASES.items():
        assert cprt_citation in cprt_text, f"{cprt_citation} missing from the CPRT fixture"
        assert ecfr_citation in ecfr_text, f"{ecfr_citation} missing from the eCFR data"
        rolled_up.setdefault(ecfr_citation, []).append(cprt_citation)

    for ecfr_citation, cprt_citations in rolled_up.items():
        target = _normalize_text(ecfr_text[ecfr_citation])
        if len(cprt_citations) == 1:
            body = _normalize_text(cprt_text[cprt_citations[0]].partition(":")[2])
            ratio = difflib.SequenceMatcher(None, body[:400], target[:400]).ratio()
            floor = KNOWN_TEXT_DRIFT.get(cprt_citations[0], 0.95)
            assert ratio > floor, (
                f"{cprt_citations[0]} -> {ecfr_citation}: requirement text only "
                f"{ratio:.2f} similar; the alias may no longer be correct"
            )
        else:
            # A CPRT-side split of one CFR paragraph (§ 164.314(b)(2)): every
            # sub-item's text must appear inside the single CFR requirement.
            for cprt_citation in cprt_citations:
                body = _normalize_text(cprt_text[cprt_citation].partition(":")[2])
                tail = re.sub(
                    r"^(i|ii|iii|iv)\s+", "", body.split("plan sponsor to", 1)[-1].strip()
                )
                assert tail[:60] in target, (
                    f"{cprt_citation} -> {ecfr_citation}: sub-item text not found in the "
                    "CFR paragraph it is rolled up into"
                )


def test_every_cprt_citation_resolves_to_a_hipaa_requirement():
    """No CPRT row may be silently dropped: each of the 68 citations must
    resolve, either directly or through `CITATION_ALIASES`."""
    from policyforge.ingest.hipaa_crosswalk_loader import apply_crosswalk

    report = apply_crosswalk(_controls(), _mapping())
    assert report.unmatched_citations == []
    assert report.unparsed_nist_ids == []


def test_apply_crosswalk_maps_standards_and_specifications():
    from policyforge.ingest.hipaa_crosswalk_loader import apply_crosswalk

    controls = _controls()
    report = apply_crosswalk(controls, _mapping())
    assert report.mapped_controls == 25
    assert report.mapped_enhancements == 40

    by_id = {c.control_id: c for c in controls}
    # Standard level.
    assert by_id["164.308(a)(5)(i)"].source_crosswalk[NIST_ANCHOR] == "AT-1, AT-2, AT-3, AT-4"
    # Implementation-specification level, mapped independently of its parent.
    specs = {e.enhancement_id: e for e in by_id["164.308(a)(1)(i)"].enhancements}
    assert specs["164.308(a)(1)(ii)(A)"].source_crosswalk[NIST_ANCHOR] == "RA-2, RA-3"
    assert specs["164.308(a)(1)(ii)(C)"].source_crosswalk[NIST_ANCHOR] == "PS-8"


def test_uncovered_requirements_are_reported_not_hidden():
    """NIST's crosswalk doesn't cover § 164.306 (general rules) or § 164.318
    (compliance dates). Those gaps are real and must be visible."""
    from policyforge.ingest.hipaa_crosswalk_loader import apply_crosswalk

    report = apply_crosswalk(_controls(), _mapping())
    assert "164.306(a)" in report.unmapped_requirements
    assert "164.318(a)" in report.unmapped_requirements
    # Nine, not ten. The tenth was `164.314(a)(2)`, which carried a title
    # and no text -- so NIST had nothing to map it to and it sat in this
    # list looking like a real gap in the crosswalk. It is no longer
    # emitted, and its three children are mapped.
    assert len(report.unmapped_requirements) == 9
    assert "164.314(a)(2)" not in report.unmapped_requirements


def test_nist_ids_are_sorted_the_way_nist_prints_them():
    """AC-2 before AC-11 (not lexically), and a control before its
    enhancements."""
    controls = _controls()
    by_id = {c.control_id: c for c in controls}
    assert (
        by_id["164.310(b)"].source_crosswalk[NIST_ANCHOR]
        == "AC-3, AC-4, AC-11, AC-12, AC-16, AC-17, AC-19, PE-3, PE-5, PL-4, PS-6"
    )
    specs = {e.enhancement_id: e for e in by_id["164.308(a)(7)(i)"].enhancements}
    assert (
        specs["164.308(a)(7)(ii)(E)"].source_crosswalk[NIST_ANCHOR]
        == "CP-2, CP-2(8), RA-2, RA-2(1)"
    )


# --------------------------------------------------------------------------
# The bundled data, and the crosswalk it feeds
# --------------------------------------------------------------------------


def test_bundled_data_matches_what_the_loader_produces():
    """data/frameworks/hipaa-security-rule/controls.json must be exactly what
    `policyforge etl-hipaa-crosswalk` generates from the pinned fixture — i.e.
    regenerated, never hand-edited."""
    from policyforge.ingest.hipaa_crosswalk_loader import apply_crosswalk

    controls = _controls()
    stored = {c.control_id: dict(c.source_crosswalk) for c in controls} | {
        e.enhancement_id: dict(e.source_crosswalk) for c in controls for e in c.enhancements
    }

    regenerated_controls = _controls()
    for control in regenerated_controls:
        control.source_crosswalk.pop(NIST_ANCHOR, None)
        for enhancement in control.enhancements:
            enhancement.source_crosswalk.pop(NIST_ANCHOR, None)
    apply_crosswalk(regenerated_controls, _mapping())

    regenerated = {c.control_id: dict(c.source_crosswalk) for c in regenerated_controls} | {
        e.enhancement_id: dict(e.source_crosswalk)
        for c in regenerated_controls
        for e in c.enhancements
    }
    assert stored == regenerated


def test_every_stored_mapping_traces_back_to_a_cprt_source_row():
    """No invented control IDs: every SP 800-53 ID in the shipped data must
    correspond to an actual row in NIST's published crosswalk."""
    from policyforge.ingest.hipaa_crosswalk_loader import (
        CITATION_ALIASES,
        _normalize_nist_id,
    )

    payload = _payload()
    fde = {
        e["elementIdentifier"][4:]: e["title"].strip()
        for e in payload["fde"]["response"]["elements"]
    }
    rde = {
        e["elementIdentifier"][4:]: e["title"].strip()
        for e in payload["rde"]["response"]["elements"]
    }
    source_pairs = {(CITATION_ALIASES.get(rde[k], rde[k]), _normalize_nist_id(fde[k])) for k in fde}

    stored_pairs = set()
    for control in _controls():
        for requirement_id, crosswalk in [(control.control_id, control.source_crosswalk)] + [
            (e.enhancement_id, e.source_crosswalk) for e in control.enhancements
        ]:
            for nist_id in crosswalk.get(NIST_ANCHOR, "").split(","):
                if nist_id.strip():
                    stored_pairs.add((requirement_id, nist_id.strip()))

    assert stored_pairs <= source_pairs
    # 279 published rows collapse to 278 stored pairs: CPRT maps both
    # § 164.314(b)(2)(i) and (ii) to PL-2, and both fold onto the single CFR
    # paragraph § 164.314(b)(2), so that pair is stored once.
    assert len(stored_pairs) == 278
    assert len(source_pairs) == 278


def test_build_crosswalk_finds_hipaa_equivalents_for_nist_controls():
    """End-to-end: with the HIPAA data loaded, `build_crosswalk` answers
    "which HIPAA requirements correspond to this NIST control?" — which is
    what `synthesize` needs in order to pull HIPAA into a topic."""
    from policyforge.mapping.crosswalk import build_crosswalk

    crosswalk = build_crosswalk(_controls())

    assert crosswalk["PS-8"]["hipaa"] == ["164.308(a)(1)(ii)(C)"]
    assert crosswalk["SC-13"]["hipaa"] == ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"]
    # An implementation specification reaches the table under its own
    # citation, not folded into its parent Standard's.
    assert "164.308(a)(1)(ii)(A)" in crosswalk["RA-3"]["hipaa"]
    assert "164.310(d)(2)(i)" in crosswalk["MP-6"]["hipaa"]
    # 108 distinct SP 800-53 controls are reachable from HIPAA.
    assert len(crosswalk) == 108


def test_synthesis_topic_pulls_hipaa_requirements_for_a_nist_anchored_topic():
    """The payoff: a topic anchored on a NIST control now includes the HIPAA
    requirements NIST maps to it."""
    from policyforge.ingest.schema import Control
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.synthesis.merge import build_synthesis_topic

    hipaa = _controls()
    nist = Control(
        control_id="SC-13",
        title="Cryptographic Protection",
        framework="NIST 800-53",
        framework_version="Rev 5",
    )
    controls = [nist, *hipaa]

    topic = build_synthesis_topic("Encryption", ["SC-13"], controls, build_crosswalk(controls))

    pulled = {c.control_id for c in topic.controls}
    assert "SC-13" in pulled
    # § 164.312(a)(2)(iv) "Encryption and decryption" lives on the
    # § 164.312(a)(1) Access control standard, which is what gets pulled in.
    assert "164.312(a)(1)" in pulled


# --------------------------------------------------------------------------
# Conservation: every skip is recorded
# --------------------------------------------------------------------------
#
# `apply_crosswalk` drops a citation with no counterpart and a NIST ID that
# did not normalize, and reports both. Against the published crosswalk it
# drops neither, so `unmatched_citations == []` and `unparsed_nist_ids == []`
# — the assertions the suite already made — hold just as well with the lines
# that append to them deleted. The reporting was untested, in the one part of
# this loader whose whole job is to make a gap visible.
#
# Every case below therefore feeds input the published data does not contain,
# and each is paired: what the guard must ALLOW comes first, because a guard
# that refuses everything passes a test that only checks it refuses.


def test_an_unmatched_citation_is_reported_rather_than_dropped():
    """The allowed case. A citation the HIPAA data does not carry is a real
    possibility — CPRT and the eCFR are two publishers on two schedules —
    and the required behaviour is to report it and carry on."""
    from policyforge.ingest.hipaa_crosswalk_loader import apply_crosswalk

    controls = _controls()
    mapping = dict(_mapping())
    mapping["164.399(z)(9)"] = ["AC-1"]

    report = apply_crosswalk(controls, mapping)

    assert report.unmatched_citations == ["164.399(z)(9)"]
    # The rest of the crosswalk still lands: one bad citation is not a
    # reason to lose the other 68.
    assert report.mapped_controls == 25
    assert report.mapped_enhancements == 40


def test_dropping_the_unmatched_report_is_refused():
    """The guard, exercised the way it would actually break: the report is
    lost while the skip stays. That is a one-line edit with no visible
    effect on the published data, and it turns a reported compliance gap
    into an absence nobody can distinguish from coverage."""
    import pytest

    from policyforge.ingest import hipaa_crosswalk_loader as loader

    mapping = dict(_mapping())
    mapping["164.399(z)(9)"] = ["AC-1"]

    report = loader.CrosswalkReport()
    with pytest.raises(ValueError, match="neither resolved nor reported unmatched"):
        loader._require_nothing_dropped(mapping, {}, {}, report)


def test_an_unparsable_nist_id_is_reported_rather_than_dropped():
    """The allowed case for the second skip path. CPRT writes the SP 800-53
    ID as free text, so a row that is not a control ID at all is possible;
    `parse_cprt_crosswalk` preserves it behind `?` precisely so this
    function can report it."""
    from policyforge.ingest.hipaa_crosswalk_loader import apply_crosswalk

    controls = _controls()
    mapping = dict(_mapping())
    mapping["164.312(a)"] = [*mapping["164.312(a)"], "?Withdrawn"]

    report = apply_crosswalk(controls, mapping)

    assert report.unparsed_nist_ids == ["Withdrawn"]
    assert report.unmatched_citations == []


def test_dropping_the_unparsed_report_is_refused():
    import pytest

    from policyforge.ingest import hipaa_crosswalk_loader as loader

    report = loader.CrosswalkReport()
    with pytest.raises(ValueError, match="did not normalize and was not reported"):
        loader._require_nothing_dropped(
            # Unaliased, so the citation is its own target ID and this
            # case turns only on the NIST ID.
            {"164.312(b)": ["?Withdrawn"]},
            {"164.312(b)": object()},
            {},
            report,
        )


def test_every_requirement_is_mapped_or_reported_uncovered():
    """The third axis, and the one the existing tests come closest to
    holding: they pin `mapped_controls`, `mapped_enhancements` and
    `len(unmapped_requirements)` as three separate numbers, which is not
    the same as saying they add up to everything that went in."""
    from policyforge.ingest.hipaa_crosswalk_loader import apply_crosswalk

    controls = _controls()
    report = apply_crosswalk(controls, _mapping())

    requirements = sum(1 + len(c.enhancements) for c in controls)
    assert (
        report.mapped_controls + report.mapped_enhancements + len(report.unmapped_requirements)
        == requirements
    )


def test_a_requirement_that_is_both_mapped_and_uncovered_is_refused():
    import pytest

    from policyforge.ingest import hipaa_crosswalk_loader as loader

    report = loader.CrosswalkReport(mapped_controls=1, unmapped_requirements=["164.312(a)(1)"])
    with pytest.raises(ValueError, match="uncovered while also carrying a mapping"):
        loader._require_nothing_dropped(
            {}, {"164.312(a)": object()}, {"164.312(a)(1)": {"AC-1"}}, report
        )


def test_apply_crosswalk_calls_the_guard(monkeypatch):
    """**Proven called, not just proven correct.**

    Three times now in this repo a guard has been written, tested against
    hand-built arguments, and never wired to the code path it was written
    for — `oscal_loader`, `arc_ampe` and `fedramp` each shipped a check
    whose unit tests all passed with the call site deleted. The tests
    above have exactly that shape: they invoke `_require_nothing_dropped`
    directly. This is the one that fails if `apply_crosswalk` stops
    calling it.
    """
    from policyforge.ingest import hipaa_crosswalk_loader as loader

    calls = []
    monkeypatch.setattr(
        loader,
        "_require_nothing_dropped",
        lambda *args, **kwargs: calls.append(args),
    )
    loader.apply_crosswalk(_controls(), _mapping())

    assert len(calls) == 1, "apply_crosswalk returned without running the conservation check"

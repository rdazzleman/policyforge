"""The HIPAA catalog's README, held to the catalog it describes.

**This is the test that should have existed before the entry was dropped.**
Removing `164.314(a)(2)` falsified five statements in a file that ships in
the wheel — the totals, the coverage line, the unmapped count, and a
sentence naming a citation the ETL no longer reports. Everything else
moved with the data: the parser, the artefact, the stamp, four test
assertions. The document about the artefact did not, and nothing asked it
to.

The same pattern exists for `cfr-42-part-2-sud-records`. It was written
two hours earlier and not carried across, which is the whole shape: a
guard that covers the catalog you were thinking about.

Numbers are derived from `controls.json` rather than pinned here, so these
fail when the README goes stale and not when the regulation moves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).parent.parent / "data" / "frameworks" / "hipaa-security-rule"


@pytest.fixture(scope="module")
def controls() -> list[dict]:
    return json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def readme() -> str:
    return (CATALOG / "README.md").read_text(encoding="utf-8")


def _counts(controls: list[dict]) -> dict[str, int]:
    enhancements = [e for c in controls for e in c["enhancements"]]
    mapped = [c for c in controls if c.get("source_crosswalk")]
    mapped += [e for e in enhancements if e.get("source_crosswalk")]
    return {
        "standards": len(controls),
        "specifications": len(enhancements),
        "requirements": len(controls) + len(enhancements),
        "mapped": len(mapped),
        "unmapped": len(controls) + len(enhancements) - len(mapped),
    }


def test_the_populated_line_matches_the_catalog(controls, readme):
    """`34 top-level Standards carrying 40 ... (74 requirements in total)`."""
    n = _counts(controls)
    stated = re.search(
        r"(\d+) top-level Standards carrying (\d+) Required/Addressable\s+"
        r"implementation specifications between them \((\d+) requirements in total\)",
        readme,
    )

    assert stated, "the README no longer states the populated counts"
    assert int(stated.group(1)) == n["standards"]
    assert int(stated.group(2)) == n["specifications"]
    assert int(stated.group(3)) == n["requirements"]


def test_the_coverage_line_matches_the_catalog(controls, readme):
    """`**65 of the 74** requirements are mapped`, and `The 9 unmapped`."""
    n = _counts(controls)
    coverage = re.search(r"\*\*(\d+) of the (\d+)\*\* requirements are mapped", readme)
    unmapped = re.search(r"The (\d+) unmapped are", readme)

    assert coverage, "the README no longer states its coverage"
    assert int(coverage.group(1)) == n["mapped"]
    assert int(coverage.group(2)) == n["requirements"]

    assert unmapped, "the README no longer states how many are unmapped"
    assert int(unmapped.group(1)) == n["unmapped"]


def test_every_citation_the_readme_names_as_unmapped_is_really_unmapped(controls, readme):
    """**The finding that blocked #140.** The README named
    `164.314(a)(2)` among the entries "reported by name each time the ETL
    runs" -- after the commit stopped the ETL reporting it. A user
    reconciling the README against the output counts nine, hunts for the
    tenth, and the tenth is the one the README points at.

    Ranges like `164.306(a)-(e)` are expanded, because the sentence uses
    them and a checker that skipped them would cover only the citations
    written out in full.
    """
    present = {c["control_id"] for c in controls}
    present |= {e["enhancement_id"] for c in controls for e in c["enhancements"]}
    mapped = {c["control_id"] for c in controls if c.get("source_crosswalk")}
    mapped |= {
        e["enhancement_id"]
        for c in controls
        for e in c["enhancements"]
        if e.get("source_crosswalk")
    }

    sentence = readme[readme.index("The ") + readme[readme.index("The ") :].index("unmapped are") :]
    sentence = sentence[: sentence.index("\n\n")]

    named: set[str] = set()
    for base, first, last in re.findall(r"164\.(\d+)\((\w)\)-\((\w)\)", sentence):
        for letter in (chr(c) for c in range(ord(first), ord(last) + 1)):
            named.add(f"164.{base}({letter})")
    # Citations written out in full. Re-adding ones the range loop already
    # produced is harmless -- it is a set -- so no subtraction is needed.
    named |= set(re.findall(r"164\.\d+\(\w\)(?:\(\w+\))*", sentence))

    assert named, "no citations parsed out of the unmapped sentence"
    for citation in sorted(named):
        assert citation in present, (
            f"the README names {citation} as an unmapped requirement, and the "
            f"catalog does not contain it at all"
        )
        assert citation not in mapped, (
            f"the README names {citation} as unmapped and it carries a mapping"
        )


def test_the_readme_does_not_name_the_dropped_entry_as_present(readme):
    """It may discuss `164.314(a)(2)` -- it does, to explain why it is
    gone and what to cite instead -- but never as something the catalog
    still reports."""
    assert "no longer emitted" in readme
    assert "164.314(a)(2)(i)" in readme, "it must point at what to cite instead"


def test_the_readme_names_every_section_whose_lead_in_is_dropped(readme, controls):
    """#268: the loader drops the "... must, in accordance with § 164.306:"
    framing sentence, and the README says which sections lose it. The set
    is DERIVED from the committed eCFR fixture, not listed here, so a
    re-pin that adds or removes such a section fails until the README
    follows. Also holds the README's claim that § 164.306 ships as five
    controls, since that is what makes the dropped link recoverable."""
    import xml.etree.ElementTree as ET

    fixture = Path(__file__).parent / "fixtures" / "ecfr_45cfr164_subpart_c.xml"
    framed = set()
    for section in ET.parse(fixture).getroot().iter("DIV8"):
        number = re.search(r"164\.3\d\d", section.findtext("HEAD") or "")
        for p in section.findall("P"):
            text = " ".join("".join(p.itertext()).split())
            if re.search(r"must, in accordance with \S+ 164\.306:$", text):
                label = number.group(0) + ("(a)" if text.startswith("(a)") else "")
                framed.add(label)
    assert len(framed) >= 2, f"derivation found too few framed sections: {framed}"

    text = " ".join(readme.split())
    start = text.index("What is left out of each statement")
    paragraph = text[start : text.index("ships as five controls", start)]
    named = set(re.findall(r"164\.3\d\d(?:\(a\))?", paragraph.split("each begin")[0]))
    assert named == framed, f"README names {sorted(named)}, the fixture frames {sorted(framed)}"

    shipped = sorted(c["control_id"] for c in controls if c["control_id"].startswith("164.306"))
    assert shipped == [f"164.306({x})" for x in "abcde"], shipped
    assert "ships as five controls" in text
    # The count of standards those sections hold, derived from the catalog:
    # a lead-in in 164.308 governs only paragraph (a), so 164.308(b) is not
    # counted. The first draft said "34", the whole catalog (9b, on #324).
    held = [
        c
        for c in controls
        if any(
            c["control_id"].startswith(s + ("(a)" if s == "164.308" else ""))
            for s in (f.replace("(a)", "") for f in framed)
        )
    ]
    assert f"each of the {len(held)} standards those sections hold" in text, len(held)

    # #268's code half: the link the README says is carried IS carried, on
    # exactly the controls this derivation (ElementTree over the fixture, a
    # different instrument from the loader's regex) says are framed.
    carrying = sorted(c["control_id"] for c in controls if "164.306" in c["related_controls"])
    assert carrying == sorted(c["control_id"] for c in held), (carrying, len(held))
    assert all(c["related_controls"] == ["164.306"] for c in held)
    assert "carried in each of those 20 controls' `related_controls`, as `164.306`" in text

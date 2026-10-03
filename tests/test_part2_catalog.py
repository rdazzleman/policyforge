"""The shipped 42 CFR Part 2 catalog, and the README that explains it.

Provenance — that `content_sha256` still matches `controls.json` — is
covered generically for every bundled catalog by `test_provenance.py`.
What is not covered generically is the thing this catalog is unusual for:
**its README makes numeric claims about the data beside it**, and it makes
them because the count is the first thing a reader will doubt. A README
that drifts from its own catalog is worse than one that says nothing,
because it is the document that exists to stop someone re-running the
parser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).parent.parent / "data" / "frameworks" / "cfr-42-part-2-sud-records"


@pytest.fixture(scope="module")
def controls() -> list[dict]:
    return json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def readme() -> str:
    return (CATALOG / "README.md").read_text(encoding="utf-8")


def test_the_shipped_catalog_is_the_two_security_sections(controls):
    """A regenerated catalog that quietly gained entries would mean the
    conduct/control test had stopped being applied. Pinned here as well as
    in the loader, because this is the file that actually ships."""
    assert [c["control_id"] for c in controls] == ["2.16", "2.19"]
    # "Substance Use Disorder Records", not "42 CFR Part 2": a declared
    # name beginning with a digit is not a legal source tag, so the
    # regulation-shaped name made the catalog impossible to cite. The
    # regulation is named in framework_version and in the README.
    assert [c["framework"] for c in controls] == ["Substance Use Disorder Records"] * 2


def test_every_shipped_requirement_has_a_distinct_citation(controls):
    """Two obligations answering to one citation is well-formed, plausible,
    and wrong only to a reader who follows the citation — which is the
    reader this catalog exists for."""
    ids = [e["enhancement_id"] for c in controls for e in c["enhancements"]]

    assert ids == sorted(set(ids), key=ids.index), f"duplicate citation in {ids}"
    assert set(ids) == {"2.16(a)", "2.16(b)", "2.19(a)", "2.19(b)"}


def test_nothing_shipped_is_empty(controls):
    """80's ruling, checked on the artefact rather than on the parser."""
    for control in controls:
        assert control["title"].strip()
        for requirement in control["enhancements"]:
            assert requirement["description"].strip(), requirement["enhancement_id"]
            assert "[Reserved]" not in requirement["description"]


def test_the_readme_count_matches_the_catalog_it_describes(controls, readme):
    """The README says "2 sections carrying 4 requirements". If the
    regulation gains a security section and someone regenerates, that
    sentence becomes false while every other check still passes."""
    requirements = sum(len(c["enhancements"]) for c in controls)
    stated = re.search(r"(\d+) sections carrying (\d+) requirements", readme)

    assert stated, "the README no longer states the count it is supposed to state"
    assert int(stated.group(1)) == len(controls)
    assert int(stated.group(2)) == requirements


def test_the_readme_names_every_control_it_ships(controls, readme):
    for control in controls:
        assert f"`{control['control_id']}`" in readme, control["control_id"]


def test_the_readme_names_what_it_rejected(readme):
    """A maintainer who sees only what was kept cannot tell thinness from
    nobody having looked. § 2.52 is the one a sceptical assessor asks
    about — it mentions sanitization and is still not a control — so it
    must be named, not just the easy § 2.66 court-order case."""
    for rejected in ("`2.52`", "`2.53`", "`2.66`"):
        assert rejected in readme, rejected

    assert "false positive" in readme, (
        "the README must keep the one term the mechanical test got wrong; "
        "a test presented as decisive is the failure this catalog avoids"
    )


def test_the_catalog_is_not_marked_unmappable():
    """Unlike 45 CFR 171, these are safeguards to implement, so `crosswalk
    seed` must treat it like any other catalog."""
    from policyforge.crosswalk.overlay import NOT_CROSSWALK_ANCHORABLE

    assert "42 CFR Part 2" not in NOT_CROSSWALK_ANCHORABLE


def test_the_readme_claims_only_what_the_search_found(readme):
    """**#264.** The README said "there is no equivalent authority mapping
    Part 2" -- the same shape of sentence #260 found false for 800-171,
    where NIST publishes the mapping in the file the catalog is built from.
    A search of 2026-09-24 (policyforge-f8, handle 5b) found none for Part 2,
    but could not enumerate NIST OLIR, where one would be registered. On
    2026-09-26 (#279) OLIR and CPRT were enumerated in full and still found
    none, so the README says so. It must still say what the search did
    NOT cover (catalog metadata, not every dataset element), so "not found"
    cannot read as "does not exist".

    **Whole sentences, after collapsing whitespace and emphasis**, because
    a fragment check passed two mutants (policyforge-78, handle 1d): the
    inverted "An equivalent mapping ... was found" contains "was found",
    and the old sentence wrapped across a line break evades a literal
    "not in". The HIPAA sentence names all three provisions § 2.16 cites,
    because an earlier draft said "only" and omitted part 160 -- which
    controls.json ships in § 2.16(b)."""
    text = " ".join(readme.replace("**", "").split())
    assert "there is no equivalent authority mapping Part 2" not in text, (
        "stronger than the search (#264)"
    )
    assert "No equivalent mapping for Part 2 was found." in text, "the claim the search supports"
    assert "the NIST OLIR and CPRT catalogs could not be listed" not in text, (
        "superseded: both were enumerated in full on 2026-09-26 (#279)"
    )
    assert "NIST OLIR, listed in full: all 102 informative references" in text, (
        "what the 2026-09-26 search covered (#279)"
    )
    assert (
        "what was searched is each catalog's metadata, not every dataset's individual elements"
        in text
    ), "the limit that stops 'not found' reading as 'does not exist'"
    assert (
        "§ 2.16 cites three HIPAA provisions: 45 CFR 164.514(b) (de-identification), "
        "and 45 CFR part 160 with part 164 Subpart D" in text
    ), "every HIPAA provision § 2.16 cites, as controls.json ships them"
    assert "Neither section this catalog ships cites anything in Subpart C" in text, (
        "why the HIPAA crosswalk does not reach this catalog"
    )
    assert "The rest of Part 2 was not read for this" in text, (
        "the measurement covers the two shipped sections, not the regulation"
    )
    # Measured on controls.json (2.16 and 2.19 only), so no sentence may
    # speak for all of Part 2 (policyforge-78, handle 1d, second round).
    for overclaim in ("Part 2 cites nothing", "does not reach Part 2"):
        assert overclaim not in text, f"{overclaim!r} speaks for sections nobody read"

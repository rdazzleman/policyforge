"""`addresses` matches a typed id in any case and reports the catalog's (#213).

80's ruling: fold case for the comparison only, `casefold()` on BOTH sides,
never upper-casing either (HIPAA ids carry lower case of their own:
`164.308(a)(1)(ii)(A)`), and report the catalog's spelling, never the typed
one. A typed id that casefolds onto more than one spelling is ambiguous: every
candidate is listed and none is picked.

The ids come from the real bundled catalogs (the other side of the check),
plus a hand-built catalog for the ambiguous case: no two of the 3,481 bundled
ids differ only by case (measured on #213), so only a catalog of the
organization's own can produce one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.ingest.schema import Control, load_controls
from policyforge.topics.bundles import requirement_view
from policyforge.topics.registry import Topic
from tests.test_bundles import write_fixtures

ROOT = Path(__file__).resolve().parent.parent
NIST = load_controls(ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json")
HIPAA = load_controls(ROOT / "data" / "frameworks" / "hipaa-security-rule" / "controls.json")
ARC = load_controls(ROOT / "data" / "frameworks" / "arc-ampe" / "controls.json")
ACCESS = Topic(name="Access Review", owner="IT Asset Management", nist_controls=["AC-2"])


@pytest.mark.parametrize("typed", ["AC-2", "ac-2", "Ac-2", "aC-2"])
def test_any_case_finds_the_control_and_reports_the_catalogs_spelling(typed):
    view = requirement_view([ACCESS], NIST, typed)
    assert not view.unknown and not view.ambiguous
    assert view.requirement_id == "AC-2"
    assert view.owned and view.claims[0].topic == "Access Review"
    assert view.render().splitlines()[0] == "AC-2"


@pytest.mark.parametrize("typed", ["AC-2(3)", "ac-2(3)", "Ac-2(3)"])
def test_an_enhancement_is_found_in_any_case(typed):
    assert requirement_view([ACCESS], NIST, typed).requirement_id == "AC-2(3)"


@pytest.mark.parametrize("typed", ["164.308(a)(1)(ii)(A)", "164.308(A)(1)(II)(a)"])
def test_a_mixed_case_hipaa_id_is_found_and_spelled_as_the_catalog_spells_it(typed):
    """Casefolding both sides, not upper-casing either: the catalog's own
    `(a)` and `(A)` both survive into the report."""
    view = requirement_view([ACCESS], NIST, typed, other_controls=HIPAA)
    assert not view.unknown, typed
    assert view.requirement_id == "164.308(a)(1)(ii)(A)"


def test_the_exact_spelling_still_works_and_prints_unchanged():
    view = requirement_view([ACCESS], NIST, "AC-2")
    assert view.requirement_id == "AC-2" and view.owned


def test_the_same_spelling_in_two_frameworks_is_not_ambiguous():
    """`AC-2` is an id in 800-53 and in ARC-AMPE alike: one spelling, so it
    is looked up as today, not reported as ambiguous."""
    view = requirement_view([ACCESS], NIST, "ac-2", other_controls=ARC)
    assert not view.ambiguous and view.requirement_id == "AC-2"


def test_an_unknown_id_is_still_unknown_and_shown_as_typed():
    view = requirement_view([ACCESS], NIST, "zz-99")
    assert view.unknown and view.requirement_id == "zz-99"


def _own(control_id: str) -> Control:
    """A control in a catalog of the organization's own (hand-built: no
    bundled catalog has two ids differing only by case)."""
    return Control(
        control_id=control_id,
        title="Own requirement",
        framework="Org Controls",
        framework_version="1",
        control_statement="An invented requirement.",
    )


def test_ids_differing_only_by_case_are_ambiguous_and_all_listed():
    own = [_own("ab-1"), _own("AB-1")]
    view = requirement_view([ACCESS], NIST, "Ab-1", other_controls=own)
    assert view.ambiguous == ["ab-1 (Org Controls)", "AB-1 (Org Controls)"]
    assert not view.claims, "none is picked"
    said = view.render()
    assert "ab-1 (Org Controls)" in said and "AB-1 (Org Controls)" in said
    assert "differ only by letter case" in said


def test_an_exact_spelling_is_still_ambiguous_when_another_differs_only_by_case():
    """The ruling: a casefolded input matching more than one id is
    ambiguous. Typing one spelling exactly doesn't pick it."""
    own = [_own("ab-1"), _own("AB-1")]
    assert requirement_view([ACCESS], NIST, "AB-1", other_controls=own).ambiguous


def test_the_command_finds_a_lower_case_id(tmp_path):
    """Through `policyforge addresses`, the user's path."""
    from click.testing import CliRunner

    from policyforge.cli import cli

    topics_path, nist_path, _ = write_fixtures(tmp_path)
    result = CliRunner().invoke(
        cli, ["addresses", "ac-2", "--topics", str(topics_path), "--controls", str(nist_path)]
    )
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert first == "AC-2", result.output
    assert "Access Review" in result.output

"""`etl-hipaa` must not silently destroy the crosswalk it does not produce.

The pipeline is two commands. `etl-hipaa` parses 45 CFR 164 Subpart C, which
carries no mapping to NIST; `etl-hipaa-crosswalk` attaches those afterwards
from the CPRT fixture. So running the first alone — the obvious thing to do
when you want a fresh provenance stamp — rewrote the catalog without them:
**65 mappings to 0**, on a file that still loaded, still held 34 controls
with correct ids, and reported nothing wrong.

It refuses rather than warning, on policyforge-1d's ruling: a warning printed
by one command in a two-command pipeline is a line nobody reads, and the loss
it describes is invisible in the result.

The tests here hold three things, and the third is the one a presence check
would miss:

* the refusal fires, exits non-zero, and leaves the file untouched
* the normal path — a catalog with no mappings yet, or a write that keeps
  them — is unaffected
* a **partial** loss is refused too. 65 to 60 is five mappings destroyed and
  passes any "are there mappings?" test.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from policyforge.cli import cli

#: A catalog shaped like the bundled one: mappings on controls *and* on
#: enhancements. Most of the real ones are on enhancements — 40 of the 65 —
#: so a fixture with only top-level mappings would pass a guard that counted
#: only the top level, which is the mistake this counts against.
WITH_MAPPINGS = [
    {
        "control_id": "164.308(a)(1)(i)",
        "title": "Security management process",
        "source_crosswalk": {"nist-800-53": "RA-1"},
        "enhancements": [
            {"enhancement_id": "164.308(a)(1)(ii)(A)", "source_crosswalk": {"nist-800-53": "RA-3"}},
            {"enhancement_id": "164.308(a)(1)(ii)(B)", "source_crosswalk": {"nist-800-53": "RA-7"}},
        ],
    },
    {
        "control_id": "164.312(a)(1)",
        "title": "Access control",
        "source_crosswalk": {"nist-800-53": "AC-3"},
        "enhancements": [],
    },
]

WITHOUT_MAPPINGS = [
    {
        "control_id": "164.308(a)(1)(i)",
        "title": "Security management process",
        "source_crosswalk": {},
        "enhancements": [
            {"enhancement_id": "164.308(a)(1)(ii)(A)", "source_crosswalk": {}},
            {"enhancement_id": "164.308(a)(1)(ii)(B)", "source_crosswalk": {}},
        ],
    },
    {
        "control_id": "164.312(a)(1)",
        "title": "Access control",
        "source_crosswalk": {},
        "enhancements": [],
    },
]


def _counted(catalog):
    from policyforge.cli.etl import _crosswalk_mappings

    return _crosswalk_mappings(catalog)


def test_the_count_includes_enhancements_not_only_controls():
    """40 of the bundled catalog's 65 mappings hang off enhancements. A guard
    that counted only the top level would call losing all 40 no change."""
    assert _counted(WITH_MAPPINGS) == 4
    assert _counted(WITHOUT_MAPPINGS) == 0


def test_the_count_does_not_depend_on_which_framework_is_mapped():
    """`nist` became `nist-800-53` in `fix/nist-family-catalog-key`. A guard
    keyed to the name would have stopped counting and said nothing."""
    renamed = json.loads(json.dumps(WITH_MAPPINGS).replace("nist-800-53", "something-else"))

    assert _counted(renamed) == 4


def _run(tmp_path: Path, existing, monkeypatch, parsed=None):
    """Run `etl-hipaa` against a catalog on disk, with the network faked."""
    out = tmp_path / "controls.json"
    out.write_text(json.dumps(existing), encoding="utf-8")

    from policyforge.ingest import hipaa_loader

    monkeypatch.setattr(hipaa_loader, "current_ecfr_date", lambda: "2026-01-01")
    monkeypatch.setattr(hipaa_loader, "fetch_ecfr_subpart_c_xml", lambda **_: "<xml/>")

    import dataclasses

    # The command calls `dataclasses.asdict` on whatever the parser returned.
    # These fixtures are already dicts, so asdict is the identity here — the
    # command's own write path is what is under test, not the parser's.
    rows = parsed if parsed is not None else WITHOUT_MAPPINGS
    monkeypatch.setattr(hipaa_loader, "parse_hipaa_security_rule", lambda _xml: rows)
    monkeypatch.setattr(dataclasses, "asdict", lambda row: row)

    result = CliRunner().invoke(cli, ["etl-hipaa", "--out", str(out)])
    return result, out


def test_a_re_parse_of_the_same_entries_keeps_every_mapping(tmp_path, monkeypatch):
    """**This test's premise moved, and the behaviour it asserted is now
    the bug rather than the fix.**

    It used to assert that a parse producing blank crosswalks is refused,
    which was right when nothing carried mappings across: the command
    would have replaced a catalog carrying 65 with one carrying none.

    But `etl-hipaa` parses a regulation that has no crosswalk in it, so
    *every* fresh parse has blank crosswalks -- and refusing them all made
    the catalog unre-parseable forever, with no `--force` and an error
    message whose first instruction was the command that had just refused.
    `_preserve_crosswalk` copies them forward by citation, so this input is
    the ordinary re-parse and must now succeed.

    The refusal it used to check is held by
    `test_a_partial_loss_is_still_refused` below, in the form that is still
    reachable: an entry the parse no longer produces.
    """
    result, out = _run(tmp_path, WITH_MAPPINGS, monkeypatch)

    assert result.exit_code == 0, result.output
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written[0]["source_crosswalk"] == {"nist-800-53": "RA-1"}
    assert written[0]["enhancements"][0]["source_crosswalk"] == {"nist-800-53": "RA-3"}
    assert written[1]["source_crosswalk"] == {"nist-800-53": "AC-3"}


def test_a_partial_loss_is_still_refused(tmp_path, monkeypatch):
    """65 to 60 passes any presence check and has destroyed five mappings.

    The loss has to be a *dropped entry* now, not a blanked field: a
    blanked field is what every re-parse produces and is carried across.
    An entry the parser no longer emits has nowhere for its mapping to go,
    so the count still falls and the write is still refused. That is the
    case worth protecting -- a parser change that quietly drops a mapped
    requirement.
    """
    fewer = json.loads(json.dumps(WITH_MAPPINGS))
    dropped = fewer[0]["enhancements"].pop(0)

    result, out = _run(tmp_path, WITH_MAPPINGS, monkeypatch, parsed=fewer)

    assert result.exit_code != 0, "a dropped mapped entry must refuse"
    assert dropped["enhancement_id"] == "164.308(a)(1)(ii)(A)"
    assert json.loads(out.read_text(encoding="utf-8")) == WITH_MAPPINGS
    assert "etl-hipaa-crosswalk" in result.output, "it must name what rebuilds mappings"


def test_it_writes_normally_when_nothing_would_be_lost(tmp_path, monkeypatch):
    """A catalog with no mappings yet — the first run of the pipeline — is
    written without complaint, which is the path that must stay open."""
    result, out = _run(tmp_path, WITHOUT_MAPPINGS, monkeypatch)

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8")) == WITHOUT_MAPPINGS


def test_it_writes_when_the_catalog_does_not_exist_yet(tmp_path, monkeypatch):
    """Nothing to lose, so nothing to refuse."""
    from policyforge.ingest import hipaa_loader

    monkeypatch.setattr(hipaa_loader, "current_ecfr_date", lambda: "2026-01-01")
    monkeypatch.setattr(hipaa_loader, "fetch_ecfr_subpart_c_xml", lambda **_: "<xml/>")
    monkeypatch.setattr(hipaa_loader, "parse_hipaa_security_rule", lambda _xml: WITHOUT_MAPPINGS)

    import dataclasses

    monkeypatch.setattr(dataclasses, "asdict", lambda row: row)
    out = tmp_path / "new" / "controls.json"

    result = CliRunner().invoke(cli, ["etl-hipaa", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()


# --------------------------------------------------------------------------
# Carrying mappings across a re-parse
# --------------------------------------------------------------------------


def _catalog(control_id, mappings, *, enhancements=()):
    return {
        "control_id": control_id,
        "title": control_id,
        "source_crosswalk": dict(mappings),
        "enhancements": [
            {"enhancement_id": eid, "title": eid, "description": "x", "source_crosswalk": dict(m)}
            for eid, m in enhancements
        ],
    }


def _write(tmp_path, catalog):
    out = tmp_path / "controls.json"
    out.write_text(json.dumps(catalog), encoding="utf-8")
    return out


def test_a_re_parse_keeps_the_mappings_the_catalog_already_had(tmp_path):
    """The reason this exists. `etl-hipaa` parses the regulation, which
    carries no crosswalk, so every fresh parse has zero mappings -- and
    `_refuse_crosswalk_loss` then refused every re-parse **forever**, with
    no `--force` on the command and an error message whose first
    instruction was the command that had just refused."""
    from policyforge.cli.etl import _preserve_crosswalk

    out = _write(
        tmp_path,
        [
            _catalog(
                "164.308(a)(1)",
                {"nist-800-53": "PM-9"},
                enhancements=[("164.308(a)(1)(ii)(A)", {"nist-800-53": "RA-3"})],
            ),
        ],
    )
    fresh = [_catalog("164.308(a)(1)", {}, enhancements=[("164.308(a)(1)(ii)(A)", {})])]

    _preserve_crosswalk(out, fresh)

    assert fresh[0]["source_crosswalk"] == {"nist-800-53": "PM-9"}
    assert fresh[0]["enhancements"][0]["source_crosswalk"] == {"nist-800-53": "RA-3"}


def test_the_guard_still_fires_when_a_mapped_entry_disappears(tmp_path):
    """**Preserving must not blunt the check.** An entry the parse no
    longer produces has nowhere to copy its mapping to, so the count still
    drops and the write is still refused. That is the case worth
    protecting: a parser change that silently drops a mapped requirement.
    """
    import click
    import pytest as _pytest

    from policyforge.cli.etl import _preserve_crosswalk, _refuse_crosswalk_loss

    out = _write(
        tmp_path,
        [
            _catalog("164.308(a)(1)", {"nist-800-53": "PM-9"}),
            _catalog("164.310(a)(1)", {"nist-800-53": "PE-3"}),
        ],
    )
    fresh = [_catalog("164.308(a)(1)", {})]  # 164.310 dropped by the parser

    _preserve_crosswalk(out, fresh)

    with _pytest.raises(click.ClickException) as caught:
        _refuse_crosswalk_loss(out, fresh)

    assert "would leave 1" in str(caught.value)


def test_the_refusal_no_longer_prescribes_the_command_that_refused(tmp_path):
    """The message used to say "Run the pipeline: policyforge etl-hipaa",
    which is the command raising it. A guard whose remedy is circular is
    worse than one that says no, because the reader spends their time
    doing what it said before concluding the tool is wrong."""
    import click
    import pytest as _pytest

    from policyforge.cli.etl import _refuse_crosswalk_loss

    out = _write(tmp_path, [_catalog("164.308(a)(1)", {"nist-800-53": "PM-9"})])

    with _pytest.raises(click.ClickException) as caught:
        _refuse_crosswalk_loss(out, [_catalog("164.308(a)(1)", {})])

    message = str(caught.value)
    assert "policyforge etl-hipaa\n" not in message
    assert "etl-hipaa-crosswalk" in message, "it must still name what rebuilds mappings"


def test_a_fresh_parse_that_carries_its_own_mapping_is_not_overwritten(tmp_path):
    """A parser that learns to read mappings itself should win over a copy
    of yesterday's file."""
    from policyforge.cli.etl import _preserve_crosswalk

    out = _write(tmp_path, [_catalog("164.308(a)(1)", {"nist-800-53": "OLD"})])
    fresh = [_catalog("164.308(a)(1)", {"nist-800-53": "NEW"})]

    _preserve_crosswalk(out, fresh)

    assert fresh[0]["source_crosswalk"] == {"nist-800-53": "NEW"}


def test_preserving_against_no_existing_catalog_is_a_no_op(tmp_path):
    """First write into an empty directory must not raise."""
    from policyforge.cli.etl import _preserve_crosswalk

    fresh = [_catalog("164.308(a)(1)", {})]

    _preserve_crosswalk(tmp_path / "absent.json", fresh)

    assert fresh[0]["source_crosswalk"] == {}


def test_an_entry_that_gains_a_citation_does_not_inherit_a_stranger_s_mapping(tmp_path):
    """Keyed by citation, so a renumbered entry gets nothing rather than
    the previous occupant's mapping."""
    from policyforge.cli.etl import _preserve_crosswalk

    out = _write(tmp_path, [_catalog("164.308(a)(1)", {"nist-800-53": "PM-9"})])
    fresh = [_catalog("164.308(a)(9)", {})]

    _preserve_crosswalk(out, fresh)

    assert fresh[0]["source_crosswalk"] == {}

"""`etl-arc-ampe` refuses a write that empties an optional column (#265).

An optional column whose caption drifts does not fail the parse: the sheet
still qualifies, every control is read, and every row reads the column as
empty. The catalog that results loads, has the right number of controls,
and has lost its guidance. These tests hold the guard to the shape named
when the issue was allocated — **extent against what shipped, not merely
non-empty** — and to the passing cases a faithful re-parse needs.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from policyforge.cli.etl import _optional_field_counts, _refuse_optional_field_loss

CATALOG = Path("data/frameworks/arc-ampe/controls.json")

GUIDANCE_CAPTION = "ARC-AMPE SUPPLEMENTAL CONTROL REQUIREMENTS & GUIDANCE"


def _control(cid: str, *, discussion="", related=(), family="Access Control", enhancements=()):
    return {
        "control_id": cid,
        "family": family,
        "discussion": discussion,
        "related_controls": list(related),
        "enhancements": [
            {"enhancement_id": eid, "additional_requirements": text} for eid, text in enhancements
        ],
    }


def _full():
    return [
        _control(
            "AC-1",
            discussion="Define roles.",
            related=["IA-1"],
            enhancements=[("AC-1(1)", "Automate.")],
        ),
        _control(
            "AC-2", discussion="Review.", related=["AC-3"], enhancements=[("AC-2(1)", "Disable.")]
        ),
    ]


def _write(tmp_path: Path, catalog: list[dict]) -> Path:
    out = tmp_path / "controls.json"
    out.write_text(json.dumps(catalog), encoding="utf-8")
    return out


def test_guidance_is_counted_in_both_places_it_lives():
    assert _optional_field_counts(_full()) == {"guidance": 4, "related": 2, "family": 2}


@pytest.mark.skipif(not CATALOG.exists(), reason="ARC-AMPE catalog not built")
def test_the_shipped_catalog_counts_what_was_measured():
    """The figures quoted in the guard's docstring and the PR, re-derived
    here so the sentence cannot outlive the catalog it describes."""
    shipped = json.loads(CATALOG.read_text(encoding="utf-8"))

    assert _optional_field_counts(shipped) == {"guidance": 306, "related": 210, "family": 215}


def test_emptying_control_guidance_is_refused(tmp_path):
    out = _write(tmp_path, _full())
    replacement = _full()
    for control in replacement:
        control["discussion"] = ""

    with pytest.raises(click.ClickException) as caught:
        _refuse_optional_field_loss(out, replacement)

    assert "guidance 4 -> 2" in str(caught.value)


def test_emptying_only_enhancement_guidance_is_refused(tmp_path):
    """**The half a one-place count would pass.** 127 of the shipped 306
    guidance entries are enhancements'; a guard reading `discussion` alone
    sees no change here at all."""
    out = _write(tmp_path, _full())
    replacement = _full()
    for control in replacement:
        for enhancement in control["enhancements"]:
            enhancement["additional_requirements"] = ""

    with pytest.raises(click.ClickException) as caught:
        _refuse_optional_field_loss(out, replacement)

    assert "guidance 4 -> 2" in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [("related_controls", [], "related 2 -> 0"), ("family", "", "family 2 -> 0")],
)
def test_emptying_related_or_family_is_refused(tmp_path, field, value, named):
    out = _write(tmp_path, _full())
    replacement = _full()
    for control in replacement:
        control[field] = value

    with pytest.raises(click.ClickException) as caught:
        _refuse_optional_field_loss(out, replacement)

    assert named in str(caught.value)
    assert "nothing has been written" in str(caught.value)


def test_a_partial_loss_is_refused_not_only_a_total_one(tmp_path):
    """**Extent, not non-empty.** One control losing its guidance leaves the
    column populated, which a non-empty check would call healthy."""
    out = _write(tmp_path, _full())
    replacement = _full()
    replacement[0]["discussion"] = ""

    with pytest.raises(click.ClickException) as caught:
        _refuse_optional_field_loss(out, replacement)

    assert "guidance 4 -> 3" in str(caught.value)


def test_a_faithful_reparse_is_allowed(tmp_path):
    """The passing case: the same content again must write, or the guard
    blocks the one re-parse it exists to protect and gets switched off."""
    out = _write(tmp_path, _full())

    _refuse_optional_field_loss(out, _full())


def test_a_rise_is_allowed(tmp_path):
    before = _full()
    before[0]["discussion"] = ""
    out = _write(tmp_path, before)

    _refuse_optional_field_loss(out, _full())


def test_a_first_write_is_allowed(tmp_path):
    _refuse_optional_field_loss(tmp_path / "controls.json", _full())


# --------------------------------------------------------------------------
# The command, end to end
# --------------------------------------------------------------------------


def _run_over_existing(tmp_path: Path, guidance_caption: str):
    """Run `etl-arc-ampe` on a two-control workbook over a catalog that
    already carries guidance for both, and return (result, out, bytes before)."""
    from click.testing import CliRunner
    from openpyxl import Workbook

    from policyforge.cli.etl import etl_arc_ampe

    book = Workbook()
    sheet = book.active
    sheet.title = "AE Mandatory Baseline"
    sheet.append(
        [
            "#",
            "Control Family",
            "Control Number",
            "Control Name",
            "ARC-AMPE CONTROL",
            guidance_caption,
            "Related Controls",
        ]
    )
    sheet.append(
        [1, "Access Control", "AC-01", "Policy", "a. Develop.", "•  Define roles.", "IA-1"]
    )
    sheet.append([2, "Access Control", "AC-02", "Accounts", "a. Define.", "•  Review.", "AC-3"])
    source = tmp_path / "volume-ii.xlsx"
    book.save(source)

    out = _write(
        tmp_path,
        [
            _control("AC-1", discussion="Define roles.", related=["IA-1"]),
            _control("AC-2", discussion="Review.", related=["AC-3"]),
        ],
    )
    before = out.read_bytes()
    result = CliRunner().invoke(
        etl_arc_ampe,
        ["--export", str(source), "--nist", str(tmp_path / "absent.json"), "--out", str(out)],
    )
    return result, out, before


def test_the_cli_refuses_a_drifted_caption_and_leaves_the_catalog_alone(tmp_path):
    """**The user's path, end to end.** A workbook whose guidance caption says
    `AND` for `&`, parsed over a catalog that has guidance: the command
    exits non-zero, prints the parse's warning naming the column, and the
    file on disk and its provenance are untouched."""
    result, out, before = _run_over_existing(tmp_path, GUIDANCE_CAPTION.replace("&", "AND"))

    assert result.exit_code != 0
    assert "WARNING: no 'guidance' column" in result.output
    assert "guidance 2 -> 0" in result.output
    assert out.read_bytes() == before
    assert not (tmp_path / "framework.yaml").exists()


def test_the_cli_writes_when_the_captions_match(tmp_path):
    """The same run with the shipped caption writes. Without this arm the
    test above would pass against a command that refused everything."""
    result, out, before = _run_over_existing(tmp_path, GUIDANCE_CAPTION)

    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.output
    assert "Wrote 2 controls" in result.output
    assert out.read_bytes() != before

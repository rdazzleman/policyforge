"""`etl-vault` must not report a file count as a control count.

Before #220 `parse_control_file` gave every field a default, so it could
not raise. An empty `.md` file became a `Control` whose ID was the
*filename*, with an empty title and an empty statement. The broad
`except` in `load_vault_controls` therefore had nothing to catch, and its
comment — *"one malformed note must not abort the whole batch, so the
failure is reported and parsing continues"* — described a path that was
never taken for the most likely malformation.

Measured on the day it was filed: five well-formed notes, one empty file
and one whose front matter was never closed gave

    Parsed 7 controls -> out.json
    exit 0, no WARN on stdout or stderr

and `ingest/schema.py:load_controls` read the result back without
complaint, empty rows included.

**Every guard below is paired: what it must ALLOW comes first.** A guard
that refuses emptiness tends to refuse thinness too, and thin-but-real is
common — the thinnest control statement in the four catalogs this project
ships is ten characters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

GOOD = """---
type: control
control_id: {cid}
family: Access Control
family_abbr: AC
title: {title}
framework: NIST 800-53
version: Rev 5
---

# {cid}: {title}

## Control Statement

> {statement}
"""


def _note(
    directory: Path,
    cid: str,
    *,
    title: str = "Account Management",
    statement: str = "Manage information system accounts.",
) -> Path:
    path = directory / f"{cid}.md"
    path.write_text(GOOD.format(cid=cid, title=title, statement=statement), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# What the guard must allow
# --------------------------------------------------------------------------


def test_a_real_but_very_thin_note_still_parses(tmp_path):
    """**The case that decides the criterion is emptiness, not thinness.**

    HIPAA 164.308(a)(5)(ii)'s control statement is exactly `Implement:` —
    ten characters, a real control whose substance lives in its children.
    Measured across all four shipped catalogs, it is the thinnest there
    is. Any threshold on length, sentence shape or word count refuses it.
    """
    from policyforge.ingest.nist_vault_loader import parse_control_file

    path = _note(tmp_path, "AC-2", title="Security Awareness", statement="Implement:")
    control = parse_control_file(path)

    assert control.control_id == "AC-2"
    assert control.control_statement == "Implement:"


def test_a_note_with_only_a_title_still_parses(tmp_path):
    """The gap the guard deliberately leaves, asserted so it is a decision
    rather than an accident.

    A note that says *something* is not refused; widening to cover it
    would rest on evidence from a different parser. See
    `_require_some_control_content`.
    """
    from policyforge.ingest.nist_vault_loader import parse_control_file

    path = tmp_path / "AC-3.md"
    path.write_text(
        "---\ntype: control\ncontrol_id: AC-3\ntitle: Access Enforcement\n---\n",
        encoding="utf-8",
    )
    control = parse_control_file(path)

    assert control.control_id == "AC-3"
    assert control.control_statement == ""


def test_a_healthy_vault_reports_every_note_parsed(tmp_path):
    from policyforge.ingest.nist_vault_loader import load_vault_controls

    for cid in ("AC-2", "AC-3", "AC-4"):
        _note(tmp_path, cid)

    report = load_vault_controls(tmp_path)

    assert report.attempted == 3
    assert len(report.controls) == 3
    assert report.unreadable == []


# --------------------------------------------------------------------------
# What it must refuse
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,content",
    [
        ("empty file", ""),
        ("front matter never closed", "---\ntype: control\ncontrol_id: AC-9\n\n# AC-9\n"),
        ("headings but no content", "# AC-9\n\n## Control Statement\n\n## Discussion\n"),
    ],
)
def test_a_contentless_note_is_refused(tmp_path, name, content):
    """The three shapes a real vault produces. Each parsed cleanly before
    #220 into a control whose ID came from the filename."""
    from policyforge.ingest.nist_vault_loader import parse_control_file

    path = tmp_path / "AC-9.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="not a control"):
        parse_control_file(path)


def test_a_refused_note_is_reported_not_dropped(tmp_path):
    """**The distinction the whole issue is about.** The refusal must
    reach the caller as a count, not only as a `WARN:` on stdout that
    nothing reads."""
    from policyforge.ingest.nist_vault_loader import load_vault_controls

    for cid in ("AC-2", "AC-3"):
        _note(tmp_path, cid)
    (tmp_path / "AC-9.md").write_text("", encoding="utf-8")

    report = load_vault_controls(tmp_path)

    assert report.attempted == 3
    assert len(report.controls) == 2
    assert len(report.unreadable) == 1
    assert "AC-9.md" in report.unreadable[0][0]


def test_every_note_is_parsed_or_reported(tmp_path):
    """Conservation, and why it is not the same as the assertions above:
    `unreadable` is empty for a healthy vault, so a test asserting it is
    empty stays green if the line that appends to it is deleted.
    `attempted` is incremented before the `try`, so the two sides move
    independently."""
    from policyforge.ingest.nist_vault_loader import load_vault_controls

    for cid in ("AC-2", "AC-3"):
        _note(tmp_path, cid)
    (tmp_path / "AC-9.md").write_text("", encoding="utf-8")

    report = load_vault_controls(tmp_path)

    assert len(report.controls) + len(report.unreadable) == report.attempted


def test_load_vault_controls_calls_the_conservation_guard(tmp_path, monkeypatch):
    """Proven called, not only proven correct — `oscal_loader`, `arc_ampe`
    and `fedramp` each shipped a guard whose unit tests passed with the
    call site deleted."""
    from policyforge.ingest import nist_vault_loader as loader

    _note(tmp_path, "AC-2")
    calls = []
    monkeypatch.setattr(loader, "_require_every_note_accounted", lambda *a, **k: calls.append(a))
    loader.load_vault_controls(tmp_path)

    assert len(calls) == 1, "load_vault_controls returned without running the check"


# --------------------------------------------------------------------------
# What the user sees
# --------------------------------------------------------------------------


def _run(directory: Path, out: Path):
    from policyforge.cli import cli

    return CliRunner().invoke(
        cli, ["etl-vault", "--controls-dir", str(directory), "--out", str(out)]
    )


def test_a_healthy_vault_exits_zero_and_says_what_it_attempted(tmp_path):
    for cid in ("AC-2", "AC-3"):
        _note(tmp_path, cid)
    out = tmp_path / "out.json"

    result = _run(tmp_path, out)

    assert result.exit_code == 0, result.output
    assert "Parsed 2 of 2 control note(s)" in result.output
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 2


def test_a_vault_with_a_bad_note_exits_non_zero_and_writes_nothing(tmp_path):
    """**The measured defect: this exited 0.** A CLI that writes a short
    catalog and reports success is how an incomplete compliance dataset
    gets committed.

    **And the first fix wrote the short catalog anyway, then exited 1.**
    The comment here used to read *"the good notes are still written: a
    bad note is not a reason to lose the rest"* — which has the reasoning
    backwards once there is an existing file. The loader keeps going so
    it can report *every* failure in one run, not so the CLI can commit a
    partial result over a good one. A catalog short by ten controls loses
    exactly what an empty one loses and looks healthier doing it.
    """
    for cid in ("AC-2", "AC-3"):
        _note(tmp_path, cid)
    (tmp_path / "AC-9.md").write_text("", encoding="utf-8")
    out = tmp_path / "out.json"
    out.write_text('["a good catalog that was already here"]', encoding="utf-8")

    result = _run(tmp_path, out)

    assert result.exit_code != 0
    assert "Nothing was written" in result.output
    assert "AC-9.md" in result.output, "the failing note must be named"
    assert out.read_text(encoding="utf-8") == '["a good catalog that was already here"]', (
        "the existing catalog was overwritten by a run that then refused"
    )


def test_an_empty_directory_exits_non_zero(tmp_path):
    """`Parsed 0 controls` and exit 0 was the old behaviour, and it is the
    same defect as `mdformat --check` with no paths exiting 0 — the
    absence of input reported as the absence of problems."""
    out = tmp_path / "out.json"
    out.write_text('["a good catalog that was already here"]', encoding="utf-8")

    result = _run(tmp_path, out)

    assert result.exit_code != 0
    assert "not an empty vault" in result.output
    # **The serious one.** A mistyped `--controls-dir` replaced a good
    # catalog with `[]` and then exited 1. Exit 1 after the damage is a
    # report, not a refusal.
    assert out.read_text(encoding="utf-8") == '["a good catalog that was already here"]', (
        "a mistyped --controls-dir destroyed the catalog it was pointed at"
    )


def test_a_directory_whose_notes_the_glob_no_longer_matches_exits_non_zero(tmp_path):
    """**"Nothing to find" and "stopped looking" must not share a value.**

    The conservation guard is an aggregate — parsed plus unreadable
    equals attempted — and an aggregate over an empty population holds
    trivially, `0 == 0`. So the population itself has to be asserted, or
    a glob that stops matching reports a clean run over nothing.

    Raised by 80 against #221, where a population guard was taken over
    the *union* of three source kinds: breaking one pathspec gave
    `exit 0, "clean across 26 source(s) (0 script, 0 workflow, 26 doc)"`.
    Same shape one layer down. Here the notes are real and the extension
    is not what `*.md` matches — a vault that stores `.markdown`, or a
    `Controls/` path pointing one directory too high.
    """
    for cid in ("AC-2", "AC-3"):
        (tmp_path / f"{cid}.markdown").write_text(
            GOOD.format(cid=cid, title="Account Management", statement="Manage accounts."),
            encoding="utf-8",
        )
    out = tmp_path / "out.json"

    result = _run(tmp_path, out)

    out.write_text('["a good catalog that was already here"]', encoding="utf-8")
    result = _run(tmp_path, out)

    assert result.exit_code != 0, (
        "two real notes were present and none were seen, and the run reported "
        f"success: {result.output}"
    )
    assert "not an empty vault" in result.output
    assert out.read_text(encoding="utf-8") == '["a good catalog that was already here"]'

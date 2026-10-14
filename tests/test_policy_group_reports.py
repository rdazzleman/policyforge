"""What `generate --policy` says when the assembler changed the draft (22,
part 2b): every removal is named in the run, in words that match what it did."""

from __future__ import annotations

from tests.test_policy_group import _policy_text, _project, _run

DRAFT = """# Access Control Policy

## Purpose

Acme controls who can reach its systems. [NIST AC-99]

## Scope

All staff.

## Identity Lifecycle Standard

Accounts are managed. [NIST AC-2] See also the Backup Standard. [HITRUST 01.a]

## Authentication Standard

People prove who they are.

## Related Documents

- Backup Standard
- Identity Lifecycle Standard

## Backup Standard

Backups are taken.
"""


def test_the_run_names_each_thing_the_assembler_removed_or_found(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, draft=DRAFT)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    out = result.output
    assert "Removed 1 citation tag(s) from the Policy" in out
    assert "Removed 2 tag-shaped span(s) that don't resolve" in out
    assert "[NIST AC-99]" in out and "[HITRUST 01.a]" in out
    assert "Dropped 2 section(s) the draft wrote" in out
    assert "'Related Documents'" in out and "'Backup Standard'" in out
    assert "the draft names Standard(s) this Policy does not govern: Backup Standard" in out
    text = _policy_text(tmp_path)
    for gone in ("[NIST AC-99]", "[HITRUST 01.a]", "## Related Documents", "## Backup Standard"):
        assert gone not in text, gone
    # The prose mention is the author's to fix: it stays, and was reported.
    assert "See also the Backup Standard." in text


def test_a_clean_draft_produces_no_assembler_report(tmp_path, monkeypatch):
    clean = (
        "# Access Control Policy\n\n## Purpose\n\nWhy.\n\n## Scope\n\nStaff.\n\n"
        "## Identity Lifecycle Standard\n\nAccounts.\n\n## Authentication Standard\n\nPeople.\n"
    )
    _project(tmp_path, monkeypatch, draft=clean)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    for quiet in ("Removed", "Dropped", "no section in the draft", "does not govern"):
        assert quiet not in result.output, quiet

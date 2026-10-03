"""The changelog guard, tested against the pull requests that motivated it.

Three real cases from 2026-09-18, and the third is the one that makes the
design work rather than merely fire:

    #119  src/policyforge/cli/etl.py, no entry          must FAIL
    #120  config/config.example.yaml + zardoz, no entry must FAIL
    #118  src/policyforge/synthesis/merge.py, no entry  must PASS, declared

#118 was a line reflow whose word-level diff against main was empty. It
touched a user-facing path and correctly needed no entry. A guard that
forced one would have taught people to write noise, and a guard people
write noise for is one they will later route around. It passes because its
author said why in the pull request body — which is the difference between
an omission and a decision, and the whole point of the check.

The file lists below are the real ones, from `git diff --name-only` on each
merge commit, rather than invented examples.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import changelog_guard as guard

PR_119 = ["src/policyforge/cli/etl.py", "tests/test_etl_hipaa_crosswalk_guard.py"]

PR_120 = [
    "README.md",
    "config/config.example.yaml",
    "docs/README.md",
    "docs/owasp-llm-top-10.md",
    "docs/responsible-ai-use.md",
    "docs/security-architecture.md",
    "docs/system-card.md",
    "evals/runner.py",
    "scripts/eval_zardoz.py",
    "src/policyforge/zardoz/answer.py",
    "src/policyforge/zardoz/shell.py",
    "src/policyforge/zardoz/startup.py",
    "tests/test_eval_harness.py",
    "tests/test_zardoz_answer.py",
]

PR_118 = ["src/policyforge/synthesis/merge.py"]


# ------------------------------------------------------- the two that shipped


def test_a_cli_change_with_no_entry_fails() -> None:
    """#119: a changed exit code on a destructive path, unannounced."""
    ok, message = guard.decide(PR_119, body="")
    assert not ok
    assert "src/policyforge/cli/etl.py" in message


def test_a_new_config_key_with_no_entry_fails() -> None:
    """#120: `entail.answering` runs a second model. The serious one."""
    ok, message = guard.decide(PR_120, body="")
    assert not ok
    assert "config/config.example.yaml" in message


def test_the_failure_names_only_the_user_facing_paths() -> None:
    """A reader must see what tripped it, not fourteen files."""
    _, message = guard.decide(PR_120, body="")
    assert "config/config.example.yaml" in message
    assert "tests/test_eval_harness.py" not in message
    assert "docs/system-card.md" not in message


# ------------------------------------------------- the one that must not trip


def test_a_declared_exemption_passes() -> None:
    """#118, with the reason its author actually gave."""
    body = (
        "Reflows one orphaned line.\n\n"
        "No changelog entry: pure reflow, word-level diff against main is empty.\n"
    )
    ok, message = guard.decide(PR_118, body)
    assert ok
    assert "word-level diff" in message


def test_a_bare_marker_with_no_reason_does_not_exempt() -> None:
    """A silent exemption wearing a word is still a silent exemption."""
    ok, _ = guard.decide(PR_118, body="No changelog entry:\n")
    assert not ok


def test_the_exemption_is_case_insensitive_and_survives_surrounding_prose() -> None:
    body = "## Notes\n\nno changelog entry: docs-only wording fix\n\nThanks.\n"
    ok, _ = guard.decide(PR_118, body)
    assert ok


# ------------------------------------------------------------ the plain cases


def test_an_entry_present_passes_whatever_else_changed() -> None:
    ok, _ = guard.decide([*PR_120, "CHANGELOG.md"], body="")
    assert ok


def test_a_test_only_change_does_not_trip() -> None:
    """A guard that fires on refactors gets routed around."""
    ok, message = guard.decide(
        ["tests/test_zardoz_answer.py", "tests/test_eval_harness.py"], body=""
    )
    assert ok
    assert "No user-facing path changed" in message


@pytest.mark.parametrize(
    "path",
    [
        "docs/security-architecture.md",
        "MEASUREMENTS.md",
        "scripts/eval_zardoz.py",
        ".github/workflows/ci.yml",
        "src/policyforge/llm/ledger.py",
    ],
)
def test_internal_and_documentation_paths_do_not_trip(path: str) -> None:
    """Deliberately out of scope, listed so the boundary is a decision.

    `MEASUREMENTS.md` is the case worth stating: four pull requests in one
    evening appended to it and none of them changed what the tool does.
    """
    ok, _ = guard.decide([path], body="")
    assert ok


@pytest.mark.parametrize("path", sorted(guard.USER_FACING))
def test_every_declared_user_facing_prefix_still_exists(path: str) -> None:
    """A prefix naming a path that has moved silently guards nothing.

    This is the failure the list form invites: a rename leaves the entry
    matching nothing, the check keeps passing, and nothing says why.
    """
    root = Path(__file__).resolve().parent.parent
    assert (root / path).exists(), f"{path} is in USER_FACING but not in the tree"


def test_the_size_of_the_examined_set_is_reported() -> None:
    """0 of 0 must not read as 0 of 14."""
    _, message = guard.decide(["tests/a.py", "tests/b.py"], body="")
    assert "2 file(s) examined" in message


def test_a_job_that_reads_the_pr_body_is_triggered_when_the_body_changes():
    """**#250.** The changelog job reads its exemption from
    `github.event.pull_request.body` -- the event PAYLOAD. With no `edited`
    type, editing the body fires nothing, and re-running the failed job
    replays the original payload and fails again, reading exactly like the
    exemption being rejected. Found by policyforge-23 (handle b5) on #234.

    Derived rather than hard-wired to the changelog job: any workflow whose
    text reads the PR body must be triggered on `edited`, so the next job
    that starts reading it is covered by the test that exists.
    """
    import yaml

    workflows = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    readers = [
        wf
        for wf in sorted(workflows.glob("*.yml"))
        if "github.event.pull_request.body" in wf.read_text(encoding="utf-8")
    ]
    assert readers, "no workflow reads the PR body; the derivation is broken"
    for wf in readers:
        spec = yaml.safe_load(wf.read_text(encoding="utf-8"))
        # PyYAML reads a bare `on:` key as the boolean True (YAML 1.1).
        on = spec.get("on", spec.get(True)) or {}
        pr = on.get("pull_request") or {}
        types = pr.get("types") if isinstance(pr, dict) else None
        assert types and "edited" in types, (
            f"{wf.name} reads the PR body but is not triggered on `edited`, so an "
            f"exemption added after the last push is invisible to every re-run"
        )

"""The gate's exit rule: a check that did not run is not a check that passed.

`scripts/check.py` used to compute its exit code as `if passed is False`,
so a skipped check — `None` — left the result untouched and the script
exited 0. A machine missing both optional tools printed seven passes and a
green exit, and two of the nine checks were the security ones (semgrep's
SAST, gitleaks' secrets scan). Three sessions reported "gate exit 0" as
evidence a branch was safe to push while gitleaks had never run.

These tests exist because the defect is invisible in the output it
produces: a summary of passes is exactly what a successful run looks like.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check

ALL_PASS = {
    "ruff (lint)": True,
    "pytest (test suite)": True,
    "semgrep (broader SAST)": True,
    "gitleaks (secrets scan)": True,
}


def test_everything_ran_and_passed_exits_zero() -> None:
    assert check.summarise(dict(ALL_PASS), set()) == 0


def test_a_real_failure_still_exits_non_zero() -> None:
    results = dict(ALL_PASS, **{"ruff (lint)": False})
    assert check.summarise(results, set()) == 1


def test_an_unacknowledged_skip_fails_the_gate() -> None:
    """The defect this file exists for: None must not read as success."""
    results = dict(ALL_PASS, **{"gitleaks (secrets scan)": None})
    assert check.summarise(results, set()) == 1


def test_both_optional_tools_missing_fails_the_gate() -> None:
    """The real-world shape — a fresh machine with neither tool installed."""
    results = dict(
        ALL_PASS,
        **{"gitleaks (secrets scan)": None, "semgrep (broader SAST)": None},
    )
    assert check.summarise(results, set()) == 1


def test_an_acknowledged_skip_passes() -> None:
    results = dict(ALL_PASS, **{"gitleaks (secrets scan)": None})
    assert check.summarise(results, {"gitleaks"}) == 0


def test_acknowledging_one_skip_does_not_acknowledge_the_other() -> None:
    """--allow-skip is per tool, so a standing flag cannot hide a new gap."""
    results = dict(
        ALL_PASS,
        **{"gitleaks (secrets scan)": None, "semgrep (broader SAST)": None},
    )
    assert check.summarise(results, {"gitleaks"}) == 1


def test_an_acknowledged_skip_does_not_rescue_a_real_failure() -> None:
    results = dict(
        ALL_PASS,
        **{"gitleaks (secrets scan)": None, "ruff (lint)": False},
    )
    assert check.summarise(results, {"gitleaks"}) == 1


def test_a_skip_with_no_declared_flag_name_stops_the_script() -> None:
    """A new skippable check that nobody added to SKIP_FLAGS.

    Without this it would fall out of the accounting and be counted as a
    pass — reintroducing the exact defect, silently, via a later edit.
    """
    results = dict(ALL_PASS, **{"a new check nobody declared": None})
    assert check.summarise(results, set()) == 2


@pytest.mark.parametrize("label", sorted(check.SKIP_FLAGS))
def test_every_declared_skippable_label_is_one_the_script_really_uses(label: str) -> None:
    """SKIP_FLAGS must name checks that exist, or the guard above is decor.

    Reads the source rather than running the suite: the labels are dict
    keys in `main`, and the point is that the two lists cannot drift.
    """
    source = Path(check.__file__).read_text(encoding="utf-8")
    assert f'"{label}"' in source


def test_no_arguments_means_no_arguments_not_whatever_sys_argv_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main()` must not read the calling process's command line.

    argparse falls back to sys.argv when handed None, so `main()` called
    from inside a pytest process tried to parse pytest's own flags and
    exited 2 on `-q`. It was caught by tests/test_tree_guard.py, which
    calls `main()` for an entirely different reason — an incidental catch,
    which is the thing this branch exists to stop relying on.
    """
    monkeypatch.setattr(sys, "argv", ["pytest", "-q", "--allow-skip", "nonsense"])
    assert check.parse_allow_skip(None) == set()


def test_allow_skip_is_parsed_when_actually_passed() -> None:
    assert check.parse_allow_skip(["--allow-skip", "gitleaks"]) == {"gitleaks"}
    assert check.parse_allow_skip(["--allow-skip", "gitleaks", "--allow-skip", "semgrep"]) == {
        "gitleaks",
        "semgrep",
    }


def test_an_unknown_tool_name_is_rejected_rather_than_ignored() -> None:
    """A typo'd flag must not silently acknowledge nothing."""
    with pytest.raises(SystemExit):
        check.parse_allow_skip(["--allow-skip", "gitleeks"])


def test_the_summary_names_what_did_not_run(capsys: pytest.CaptureFixture[str]) -> None:
    """Stated before the wall of PASS, since that is what gets read."""
    results = dict(ALL_PASS, **{"gitleaks (secrets scan)": None})
    check.summarise(results, set())
    out = capsys.readouterr().out
    assert "DID NOT RUN" in out
    assert "gitleaks (secrets scan)" in out
    assert "--allow-skip gitleaks" in out
    # The counts, so "0 of 0" can never be misread as "0 of 9".
    assert "3 ran, 0 failed, 1 skipped" in out


# ---- semgrep's lock in advisory mode (#82; 80's ruling 1774, condition 2) ----

LABEL = "pip-audit (semgrep's lock)"


def _audit_prints(monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int) -> None:
    def run(cmd, **kwargs):
        return check.subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(check.subprocess, "run", run)
    monkeypatch.setattr(check, "NOTES", {})


def test_a_pass_with_advisories_carries_its_count_into_the_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Never a bare PASS: the summary repeats the count under the step."""
    line = "semgrep lock: 8 advisories (advisory, not blocking; #82)"
    _audit_prints(monkeypatch, f"  ADVISORY  pyjwt 2.13.0: CVE-1\n{line}\n", 0)
    assert check.check_semgrep_lock(LABEL) is True
    check.summarise({**ALL_PASS, LABEL: True}, set())
    out = capsys.readouterr().out
    assert f"PASS  {LABEL}\n        {line}" in out


def test_an_audit_that_never_counted_fails_whatever_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Condition 2: an audit that can't run isn't an empty report. Exit 0
    with no count line means it never got as far as counting."""
    _audit_prints(monkeypatch, "pip-audit over requirements/semgrep/semgrep.txt\n", 0)
    assert check.check_semgrep_lock(LABEL) is False
    assert check.NOTES[LABEL].startswith("semgrep lock: NOT COUNTED")


def test_a_blocking_finding_fails_even_with_a_count(monkeypatch: pytest.MonkeyPatch) -> None:
    line = "semgrep lock: 0 advisories (advisory, not blocking; #82)"
    _audit_prints(monkeypatch, f"  SHARED WITH THE PRODUCT  x\n{line}\n", 1)
    assert check.check_semgrep_lock(LABEL) is False

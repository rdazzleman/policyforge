"""A requested eval suite that contributes nothing is named, not dropped (#223).

Both guards used to run over the union. `if not planned` in the harness was
satisfied by any one suite's cases, and `format_report` skipped a suite with
no results. So a run of routing and synthesis, with synthesis empty,
produced a clean routing report whose totals were true about what ran and
silent about what did not. That report is what becomes an epoch.

**The dangerous case is the partial one**, some suites present and one
empty, because an empty run already fails loudly. So that is the case
seeded here, and each guard also gets a passing arm: a guard that refuses
every run passes a refusal test just as well.

**Assert the suite LINE, not the word.** The report prints each prompt's
provenance, and `synthesis.merge` is one of them, so "synthesis" appears
in a report that never ran the synthesis suite. Measured while filing
this, and it is why these tests match `synthesis: NOT RUN` at the start
of a line.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from evals.runner import SUITES, CaseResult, Outcome, format_report, load_cases


def _lines(report: str) -> list[str]:
    return report.splitlines()


# --------------------------------------------------------------------------
# format_report
# --------------------------------------------------------------------------


def test_a_requested_suite_with_no_results_gets_its_own_line():
    results = [CaseResult("routing", "a", [Outcome(True)])]

    report = format_report(results, repeat=1, requested=["routing", "synthesis"])

    assert "synthesis: NOT RUN, 0 cases, so this report says nothing about it" in _lines(report)
    assert "1 case(s) x 1 run(s), 1 of 2 requested suite(s) measured" in _lines(report)
    assert "  1 requested suite(s) NOT RUN: synthesis" in _lines(report)


def test_a_partial_run_does_not_claim_every_case_passed():
    """True of the cases, false of the run, and it is the line a reader
    stops at."""
    results = [CaseResult("routing", "a", [Outcome(True)])]

    report = format_report(results, repeat=1, requested=["routing", "synthesis"])

    assert "  every case that ran passed every run" in _lines(report)
    assert "  every case passed every run" not in _lines(report)


def test_a_complete_run_says_nothing_about_absence():
    """The passing arm: every requested suite ran, so no NOT RUN line."""
    results = [CaseResult("routing", "a", [Outcome(True)])]

    report = format_report(results, repeat=1, requested=["routing"])

    assert not any("NOT RUN" in line for line in _lines(report))
    assert "1 case(s) x 1 run(s), 1 of 1 requested suite(s) measured" in _lines(report)
    assert "  every case passed every run" in _lines(report)


def test_the_requested_set_cannot_be_left_out():
    """No default, so a caller that forgets it fails rather than getting the
    report that cannot see an absence."""
    with pytest.raises(TypeError):
        format_report([], repeat=1)  # type: ignore[call-arg]


def test_a_misspelt_suite_is_refused_rather_than_reported_absent():
    with pytest.raises(ValueError, match="not a suite: synthesys"):
        format_report([], repeat=1, requested=["synthesys"])


# --------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------


@pytest.fixture
def routing_only(tmp_path: Path) -> Path:
    """A cases file holding two routing cases and nothing else."""
    shipped = load_cases()
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump({"routing": shipped["routing"][:2]}), encoding="utf-8")
    return path


def _dry_run(monkeypatch, capsys, *argv: str) -> tuple[int, str]:
    from scripts import eval_zardoz

    monkeypatch.setattr(sys, "argv", ["eval_zardoz.py", "--dry-run", *argv])
    code = eval_zardoz.main()
    return code, capsys.readouterr().out


def test_a_requested_suite_with_no_cases_refuses_the_run(monkeypatch, capsys, routing_only):
    """**The partial case.** Routing has cases, synthesis has none, and both
    were asked for by name. The old guard saw two planned cases and ran."""
    code, out = _dry_run(
        monkeypatch,
        capsys,
        "--cases",
        str(routing_only),
        "--suite",
        "routing",
        "--suite",
        "synthesis",
    )

    assert code == 1
    assert "No cases for requested suite(s), so nothing was run: synthesis" in out
    assert "case(s) x" not in out, "refused before planning a single call"


def test_the_same_request_with_cases_for_both_runs(monkeypatch, capsys, routing_only):
    """The passing arm for the refusal above."""
    code, out = _dry_run(monkeypatch, capsys, "--cases", str(routing_only), "--suite", "routing")

    assert code == 0
    assert "2 case(s) x 3 run(s) = 6 calls" in out
    assert "NOT RUN" not in out


def test_a_limit_that_empties_a_suite_is_caught_too(monkeypatch, capsys, routing_only):
    """The check reads what was PLANNED, not what the file holds, so a
    suite emptied after loading is counted the same way."""
    code, out = _dry_run(
        monkeypatch, capsys, "--cases", str(routing_only), "--suite", "routing", "--limit", "-2"
    )

    assert code == 1
    assert "nothing was run: routing" in out


def test_a_default_run_names_the_suites_its_cases_file_leaves_empty(
    monkeypatch, capsys, routing_only
):
    """No `--suite`, so a file covering some suites is a legitimate run of
    those: it proceeds, and names the rest rather than omitting them."""
    code, out = _dry_run(monkeypatch, capsys, "--cases", str(routing_only))

    assert code == 0
    (not_run,) = [line for line in out.splitlines() if line.startswith("NOT RUN, no cases:")]
    named = not_run.removeprefix("NOT RUN, no cases: ").split(", ")
    assert "synthesis" in named
    assert "routing" not in named


# --------------------------------------------------------------------------
# What distinguishes a legitimately empty suite from one that stopped
# --------------------------------------------------------------------------


def test_the_shipped_cases_fill_every_suite():
    """**Extent against what ships, not a runtime judgement.** A default run
    tolerates an empty suite because a custom cases file may cover a
    subset. The shipped file may not, so a suite that silently stops
    contributing, the way synthesis did between its registration and its
    first case, fails here rather than becoming a NOT RUN line in an
    epoch nobody reads twice."""
    cases = load_cases()

    empty = sorted(suite for suite in SUITES if not cases.get(suite))

    assert empty == [], f"shipped with no cases: {', '.join(empty)}"


# --------------------------------------------------------------------------
# The real run, not the dry run
# --------------------------------------------------------------------------


class _Fake:
    """Answers the reachability probe and meters nothing. No call leaves."""

    model = "fake"

    def generate(self, **_):
        return "ok"

    def summary(self) -> str:
        return "0 model call(s); fake"


def test_the_real_run_names_the_empty_suite_in_the_epoch_report(monkeypatch, capsys, routing_only):
    """**The path that writes the epoch** (9b's finding on #274). Every test
    above uses `--dry-run`, which returns before the report is printed, so
    the report's `requested=` argument and the real-run NOT RUN line had no
    test at all: passing `requested` as the suites that PLANNED, rather than
    the suites asked for, brought #223 back on exactly this path and every
    test stayed green.

    `run_case` is stubbed because grading is not what is under test here;
    the wiring from what was requested to what the report says is.
    """
    from scripts import eval_zardoz

    monkeypatch.setattr(eval_zardoz, "eval_config", lambda **_: {"llm": {"provider": "fake"}})
    monkeypatch.setattr(eval_zardoz, "build_provider", lambda _config: _Fake())
    monkeypatch.setattr(
        eval_zardoz,
        "run_case",
        lambda suite, case, *_a, **_k: CaseResult(suite, case["name"], [Outcome(True)]),
    )
    monkeypatch.setattr(
        sys, "argv", ["eval_zardoz.py", "--cases", str(routing_only), "--repeat", "1"]
    )

    code = eval_zardoz.main()
    out = capsys.readouterr().out.splitlines()

    assert code == 0
    assert any(line.startswith("Running ") for line in out), "took the real-run branch"
    assert any(line.startswith("NOT RUN, no cases:") and "synthesis" in line for line in out)
    assert "synthesis: NOT RUN, 0 cases, so this report says nothing about it" in out
    assert "  every case that ran passed every run" in out

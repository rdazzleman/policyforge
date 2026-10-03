"""The plan runs what was asked for, and says where it differs (#275, #276).

Two ways the harness planned a population the command line did not state:

- **#275.** A `--cases` file gained the 66 shipped paraphrase cases whatever
  it said, and the shipped rewordings of any answering case whose name
  matched a shipped parent. A file of two routing cases planned 68, and a
  file's own `paraphrase:` rows were replaced by the shipped set. 80's
  ruling: **the file you name is the whole population.**
- **#276.** `--suite routing --suite routing` planned the 18 routing cases
  twice, doubling the spend and counting each case twice in the epoch.

Both are fixed by planning exactly what was asked and **printing a line
wherever the plan differs from the command line**, because a silent
correction is one more population change nobody can see. Every notice here
has a quiet twin: a notice that also fires on an ordinary run is one people
learn to read past. And the real run is tested as well as the dry run,
because the dry run returns before the report that becomes an epoch (9b's
finding on #274).

Every measurement here is a dry run or a stubbed run, so it costs $0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from evals.runner import (
    DEFAULT_CASES,
    GENERATED_SUITES,
    CaseResult,
    Outcome,
    load_answer_paraphrases,
    load_cases,
    load_paraphrases,
)

SHIPPED = load_cases()


def _file(tmp_path: Path, content: dict) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def _run(monkeypatch, capsys, *argv: str) -> tuple[int, list[str]]:
    from scripts import eval_zardoz

    monkeypatch.setattr(sys, "argv", ["eval_zardoz.py", *argv])
    code = eval_zardoz.main()
    return code, capsys.readouterr().out.splitlines()


def _planned(out: list[str]) -> int:
    (line,) = [line for line in out if " case(s) x " in line and "calls" in line]
    return int(line.split()[0])


# --------------------------------------------------------------------------
# #275: a --cases file is the whole population
# --------------------------------------------------------------------------


def test_a_routing_only_file_loads_only_routing(tmp_path):
    cases = load_cases(_file(tmp_path, {"routing": SHIPPED["routing"][:2]}))

    assert set(cases) == {"routing"}


def test_an_answering_case_does_not_pull_in_its_shipped_rewordings(tmp_path):
    """The derivation 80's extended ruling rejected: the parent's name
    matching a shipped parent was enough to add its rewordings."""
    parent = SHIPPED["answering"][0]
    assert load_answer_paraphrases(parents=[parent]), "premise: this parent HAS shipped rewordings"

    cases = load_cases(_file(tmp_path, {"answering": [parent]}))

    assert "answer_paraphrase" not in cases


def test_a_files_own_paraphrase_rows_are_the_ones_planned(tmp_path):
    """They used to be replaced by the shipped 66: the file stated one
    population and a different one ran."""
    own = [dict(SHIPPED["routing"][0], name=f"own-{i}") for i in range(2)]

    cases = load_cases(_file(tmp_path, {"paraphrase": own}))

    assert [c["name"] for c in cases["paraphrase"]] == ["own-0", "own-1"]


def test_without_a_file_the_shipped_generated_suites_still_load():
    """The default is unchanged. Compared against the loaders themselves, so
    this does not pin today's counts."""
    assert SHIPPED["paraphrase"] == load_paraphrases() != []
    assert SHIPPED["answer_paraphrase"] == load_answer_paraphrases(SHIPPED["answering"]) != []


def test_the_plan_prints_what_a_file_leaves_out(monkeypatch, capsys, tmp_path):
    path = _file(tmp_path, {"routing": SHIPPED["routing"][:2]})

    code, out = _run(monkeypatch, capsys, "--cases", str(path), "--dry-run")

    assert code == 0
    assert _planned(out) == 2, "68 before #275: the shipped paraphrases rode along"
    assert "paraphrase  0  (not in --cases file)" in out
    assert "answer_paraphrase  0  (not in --cases file)" in out


def test_no_file_means_no_exclusion_line(monkeypatch, capsys):
    """Quiet twin: with no --cases, nothing is excluded, so nothing says so."""
    code, out = _run(monkeypatch, capsys, "--dry-run")

    assert code == 0
    assert not any("(not in --cases file)" in line for line in out)


def test_a_file_that_names_paraphrase_gets_no_exclusion_line_for_it(monkeypatch, capsys, tmp_path):
    """Quiet twin at the file level: the notice is about THIS file."""
    own = [dict(SHIPPED["routing"][0], name="own")]
    path = _file(tmp_path, {"routing": SHIPPED["routing"][:1], "paraphrase": own})

    _, out = _run(monkeypatch, capsys, "--cases", str(path), "--dry-run")

    assert _planned(out) == 2
    assert "paraphrase  0  (not in --cases file)" not in out
    assert "answer_paraphrase  0  (not in --cases file)" in out


def test_a_suite_not_asked_for_gets_no_exclusion_line(monkeypatch, capsys, tmp_path):
    """`--suite routing` does not ask for paraphrase, so its absence from the
    file is not a difference from the command line."""
    path = _file(tmp_path, {"routing": SHIPPED["routing"][:2]})

    _, out = _run(monkeypatch, capsys, "--cases", str(path), "--suite", "routing", "--dry-run")

    assert not any("(not in --cases file)" in line for line in out)


def test_the_shipped_file_named_explicitly_is_a_file_like_any_other(monkeypatch, capsys):
    """**Pinned, at 80's ruling on #280: no special case.** The switch is
    whether a path was GIVEN, not whether it equals the default, so
    spelling out `evals/cases.yaml` plans what that file names: 101 today
    against 192 by default. 9b found this unpinned: special-casing the
    shipped path back to 192 left every test green."""
    assert not set(GENERATED_SUITES) & set(load_cases(DEFAULT_CASES))

    _, out = _run(monkeypatch, capsys, "--cases", str(DEFAULT_CASES), "--dry-run")

    from_file = sum(len(v) for k, v in SHIPPED.items() if k not in GENERATED_SUITES)
    assert _planned(out) == from_file < sum(len(v) for v in SHIPPED.values())
    assert "paraphrase  0  (not in --cases file)" in out
    assert "answer_paraphrase  0  (not in --cases file)" in out


def test_a_refused_run_says_why_before_it_refuses(monkeypatch, capsys, tmp_path):
    """**The order is the point** (1d's finding on #280). The #223 refusal
    says a requested suite has no cases; the exclusion line says it is
    because the file does not name it. Printed after the refusal, it is
    never printed at all, and moving it there left every test green."""
    path = _file(tmp_path, {"routing": SHIPPED["routing"][:2]})

    code, out = _run(
        monkeypatch, capsys, "--cases", str(path), "--suite", "paraphrase", "--dry-run"
    )

    assert code == 1
    reason = out.index("paraphrase  0  (not in --cases file)")
    refusal = out.index("No cases for requested suite(s), so nothing was run: paraphrase")
    assert reason < refusal


def test_a_refused_ordinary_suite_gets_no_exclusion_line(monkeypatch, capsys, tmp_path):
    """Quiet twin: synthesis is not generated, so its absence from a file is
    not an exclusion, and the refusal stands alone."""
    path = _file(tmp_path, {"routing": SHIPPED["routing"][:2]})

    code, out = _run(monkeypatch, capsys, "--cases", str(path), "--suite", "synthesis", "--dry-run")

    assert code == 1
    assert out == ["No cases for requested suite(s), so nothing was run: synthesis"]


# --------------------------------------------------------------------------
# #276: a suite named twice is planned once, and the plan says so
# --------------------------------------------------------------------------


def test_a_suite_named_twice_is_planned_once_and_says_so(monkeypatch, capsys):
    _, once = _run(monkeypatch, capsys, "--suite", "routing", "--dry-run")
    code, twice = _run(monkeypatch, capsys, "--suite", "routing", "--suite", "routing", "--dry-run")

    assert code == 0
    assert _planned(twice) == _planned(once) == len(SHIPPED["routing"])
    assert "routing named 2 times; planned once" in twice


def test_a_suite_named_once_gets_no_repeat_line(monkeypatch, capsys):
    """Quiet twin."""
    _, out = _run(monkeypatch, capsys, "--suite", "routing", "--suite", "synthesis", "--dry-run")

    assert not any("planned once" in line for line in out)
    assert _planned(out) == len(SHIPPED["routing"]) + len(SHIPPED["synthesis"])


def test_suites_are_planned_in_the_order_first_named(monkeypatch, capsys):
    """What `dict.fromkeys` buys over a set: the plan reads in the order the
    command line did. Only print order depends on it (9b, non-blocking)."""
    _, out = _run(
        monkeypatch,
        capsys,
        "--suite",
        "synthesis",
        "--suite",
        "routing",
        "--suite",
        "synthesis",
        "--dry-run",
    )

    suites = [line.split()[0] for line in out if line.startswith("  ")]
    assert suites == ["synthesis"] * len(SHIPPED["synthesis"]) + ["routing"] * len(
        SHIPPED["routing"]
    )


def test_each_repeated_suite_is_named_with_its_own_count(monkeypatch, capsys):
    _, out = _run(
        monkeypatch,
        capsys,
        *("--suite", "routing") * 3,
        *("--suite", "synthesis") * 2,
        "--dry-run",
    )

    assert "routing named 3 times; planned once" in out
    assert "synthesis named 2 times; planned once" in out
    assert _planned(out) == len(SHIPPED["routing"]) + len(SHIPPED["synthesis"])


# --------------------------------------------------------------------------
# The real run, where the spend and the epoch happen
# --------------------------------------------------------------------------


class _Fake:
    """Answers the reachability probe. No call leaves."""

    model = "fake"

    def generate(self, **_):
        return "ok"

    def summary(self) -> str:
        return "0 model call(s); fake"


@pytest.fixture
def graded(monkeypatch):
    """Stub the provider and the grading, and record every case graded."""
    from scripts import eval_zardoz

    calls: list[tuple[str, str]] = []

    def run_case(suite, case, *_a, **_k):
        calls.append((suite, case["name"]))
        return CaseResult(suite, case["name"], [Outcome(True)])

    monkeypatch.setattr(eval_zardoz, "eval_config", lambda **_: {"llm": {"provider": "fake"}})
    monkeypatch.setattr(eval_zardoz, "build_provider", lambda _config: _Fake())
    monkeypatch.setattr(eval_zardoz, "run_case", run_case)
    return calls


def test_the_real_run_grades_each_case_once_however_often_its_suite_is_named(
    monkeypatch, capsys, tmp_path, graded
):
    path = _file(tmp_path, {"routing": SHIPPED["routing"][:2]})

    code, out = _run(
        monkeypatch, capsys, "--cases", str(path), *("--suite", "routing") * 2, "--repeat", "1"
    )

    assert code == 0
    assert any(line.startswith("Running 2 case(s)") for line in out), "took the real-run branch"
    assert "routing named 2 times; planned once" in out
    assert len(graded) == 2 == len(set(graded)), "4 before #276: each case graded twice"
    assert "2 case(s) x 1 run(s), 1 of 1 requested suite(s) measured" in out


def test_the_real_run_grades_only_what_the_file_names(monkeypatch, capsys, tmp_path, graded):
    path = _file(tmp_path, {"routing": SHIPPED["routing"][:2]})

    code, out = _run(monkeypatch, capsys, "--cases", str(path), "--repeat", "1")

    assert code == 0
    assert {suite for suite, _ in graded} == {"routing"}, "68 before #275"
    assert len(graded) == 2
    assert "paraphrase  0  (not in --cases file)" in out
    assert "answer_paraphrase  0  (not in --cases file)" in out

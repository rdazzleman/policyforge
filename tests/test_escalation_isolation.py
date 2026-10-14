"""A pending escalation never crosses from one test to the next (#315).

`tests/conftest.py`'s `escalations_start_empty` gives each test a fresh
pending list. The leak needed two tests in one worker, in one order, which
xdist only sometimes produces, so it is reproduced deterministically here: a
two-test module run in one process, without xdist, once with the real
fixture and once without it. Without it the second test reads the first's
record, so the pair can see the leak; with it, it can't happen.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PAIR = """
from policyforge.llm import escalation


class Record:
    def as_dict(self):
        return {"model": "left-behind"}


def test_announces_and_never_takes():
    pending = escalation._pending.get()
    if pending is None:  # as escalation.announce does
        pending = []
        escalation._pending.set(pending)
    pending.append(Record())


def test_takes_nothing_it_did_not_announce():
    assert escalation.take() == ()
"""


def _run_pair(tmp_path: Path, conftest: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "conftest.py").write_text(conftest, encoding="utf-8")
    (tmp_path / "test_pair.py").write_text(PAIR, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)])}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:xdist", "-p", "no:cacheprovider",
         "-p", "no:randomly", str(tmp_path / "test_pair.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )  # fmt: skip


def test_without_the_fixture_the_next_test_reads_a_leaked_record(tmp_path):
    """The control: the pair does see the leak #315 is about."""
    run = _run_pair(tmp_path, "")
    assert run.returncode == 1, run.stdout[-2000:]
    assert "1 failed, 1 passed" in run.stdout and "left-behind" in run.stdout


def test_with_the_fixture_no_record_crosses_to_the_next_test(tmp_path):
    run = _run_pair(tmp_path, "from tests.conftest import escalations_start_empty  # noqa: F401\n")
    assert run.returncode == 0, run.stdout[-2000:]
    assert "2 passed" in run.stdout

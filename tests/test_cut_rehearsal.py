"""The cut is rehearsed on HEAD before it assembles the fragments (#120).

#118: a test read a changelog fragment by name, passed on every train gate,
and failed on the release PR, the first tree without fragments. The cut now
rehearses itself in a throwaway worktree and runs the suite there first
(`scripts/cut_rehearsal.py`, in the gate of `release.py`'s `changelog`
step). 1d measured the rehearsal on #120 with #118's shape (it fails, where
the plain gate passes); these pin the wiring and the decisions around it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import cut_rehearsal  # noqa: E402
import release  # noqa: E402

# --- release.py runs it before the fragments are assembled --------------------


def _changelog_gate():
    (step,) = [s for s in release.steps("forgejo", release.PUBLISH_LOCAL) if s.key == "changelog"]
    return step.gate


def _ctx(**kwargs) -> release.Context:
    return release.Context(version="9.9.9", **kwargs)


def test_the_changelog_gate_fails_when_the_rehearsal_fails(monkeypatch):
    monkeypatch.setattr(release, "_fragments_ready", lambda ctx: release.Check(True, ["ok"]))
    failed = release.Check(False, ["cut rehearsal: FAILED"])
    gate = _changelog_gate()(_ctx(rehearse=lambda version, root: failed))
    assert not gate.ok and "cut rehearsal: FAILED" in gate.measured


def test_the_changelog_gate_holds_when_both_hold(monkeypatch):
    monkeypatch.setattr(release, "_fragments_ready", lambda ctx: release.Check(True, ["ok"]))
    seen = []

    def rehearse(version, root):
        seen.append((version, root))
        return release.Check(True, ["cut rehearsal: passed"])

    assert _changelog_gate()(_ctx(rehearse=rehearse)).ok
    assert seen == [("9.9.9", release.REPO_ROOT)]


def test_a_context_with_no_rehearsal_fails_the_gate_rather_than_skipping_it(monkeypatch):
    monkeypatch.setattr(release, "_fragments_ready", lambda ctx: release.Check(True, ["ok"]))
    gate = _changelog_gate()(_ctx())
    assert not gate.ok and any("NOT RUN" in line for line in gate.measured)


def test_main_hands_the_real_rehearsal_to_the_cut(monkeypatch):
    seen = []
    monkeypatch.setattr(release, "run_steps", lambda plan, ctx, execute: seen.append(ctx) or 0)
    # main refuses the Forgejo forge without a token; run_steps is faked, so
    # this placeholder reaches nothing.
    monkeypatch.setenv("FORGEJO_TOKEN", "not-a-real-token")
    assert release.main(["9.9.9"]) == 0
    assert seen and seen[0].rehearse is release._real_rehearse


# --- cut_rehearsal.rehearse, with its subprocesses faked -----------------------


class Fake:
    """Answers git and the cut program; records every call, in order."""

    def __init__(self, cut_ok=True, tests_ok=True):
        self.calls: list[list[str]] = []
        self.cut_ok, self.tests_ok = cut_ok, tests_ok

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if "-c" in argv:  # the cut program
            code = 0 if self.cut_ok else 1
            return subprocess.CompletedProcess(argv, code, "cut step changelog: done\n", "")
        if "pytest" in argv:
            return subprocess.CompletedProcess(argv, 0 if self.tests_ok else 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def did(self, word: str) -> bool:
        return any(word in call for call in self.calls)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    def make(**kwargs):
        runner = Fake(**kwargs)
        monkeypatch.setattr(cut_rehearsal.subprocess, "run", runner)
        # The worktree directory, inside the test's own: the fake git never
        # fills or removes it, so rehearse() must remove it itself.
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.setattr(cut_rehearsal.tempfile, "mkdtemp", lambda prefix: str(work))
        runner.work = work
        return runner

    return make


def test_a_pass_runs_the_cut_then_the_suite_and_is_remembered(fake, tmp_path):
    runner = fake()
    assert cut_rehearsal.rehearse("9.9.9", passes=tmp_path, out=lambda *_: None)
    order = [
        next((w for w in ("add", "-c", "commit", "pytest", "remove") if w in c), "")
        for c in runner.calls
    ]
    order = [w for w in order if w]
    assert order == ["add", "-c", "add", "commit", "pytest", "remove"]
    assert (tmp_path / f"{'a' * 40}-9.9.9.ok").exists()
    assert not runner.work.exists(), "its empty worktree directory is removed"


def test_a_remembered_pass_for_the_same_tree_is_not_run_again(fake, tmp_path):
    (tmp_path / f"{'a' * 40}-9.9.9.ok").write_text("9.9.9\n", encoding="utf-8")
    runner = fake()
    assert cut_rehearsal.rehearse("9.9.9", passes=tmp_path, out=lambda *_: None)
    assert not runner.did("pytest") and not runner.did("worktree")


def test_a_failing_suite_is_not_remembered_and_the_worktree_still_goes(fake, tmp_path):
    runner = fake(tests_ok=False)
    assert not cut_rehearsal.rehearse("9.9.9", passes=tmp_path, out=lambda *_: None)
    assert runner.calls[-1][-3:-1] == ["remove", "--force"]
    assert not list(tmp_path.iterdir())


def test_a_cut_that_does_not_hold_fails_before_any_test(fake, tmp_path):
    runner = fake(cut_ok=False)
    assert not cut_rehearsal.rehearse("9.9.9", passes=tmp_path, out=lambda *_: None)
    assert not runner.did("pytest")
    assert runner.calls[-1][-3:-1] == ["remove", "--force"]


def test_a_partial_run_is_never_remembered_as_the_suite(fake, tmp_path):
    fake()
    assert cut_rehearsal.rehearse("9.9.9", ["tests/x.py"], passes=tmp_path, out=lambda *_: None)
    assert not list(tmp_path.iterdir())


def test_the_cut_program_runs_release_pys_own_steps_in_its_order():
    """Not a copy of the bump: the tree's release.py, its gates and postconditions."""
    program = cut_rehearsal._CUT
    names = ["_fragments_ready", "_assemble", "_changelog_cut", "_bump_ready", "_bump", "_bumped"]
    names += ["_pins_ready", "_repin", "_pins_current"]
    positions = [program.index(f"release.{name}") for name in names]
    assert positions == sorted(positions)
    for name in names:
        assert callable(getattr(release, name)), name

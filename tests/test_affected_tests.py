"""Selecting the tests a change affects, and recording what selects them (#291).

`scripts/affected_tests.py` copies the batch gate's testmon data and lets it
select; `scripts/check.py --record-selection` records that data on the gate's
full run. 1d measured the selection itself on the issue (five merged fixes
replayed, every failing test selected); these pin the decisions around it:
which data is used, that no usable data falls back to the targeted set rather
than to nothing, that an empty selection is a failure, that the scanners
always run, and that the gate still runs everything.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import affected_tests as at  # noqa: E402
import check  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **env},
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> tuple[str, str]:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^{tree}")


def _data(path: Path, tests: int = 1) -> Path:
    db = sqlite3.connect(path)
    db.execute("create table test_execution (id integer)")
    db.executemany("insert into test_execution values (?)", [(i,) for i in range(tests)])
    db.commit()
    db.close()
    return path


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    return path


# --- which data ---------------------------------------------------------------


@needs_git
def test_the_snapshot_of_the_nearest_ancestors_tree_is_used(repo, tmp_path):
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    _, old_tree = _commit(repo, "a", "1")
    middle, middle_tree = _commit(repo, "b", "2")
    _commit(repo, "c", "3")
    _data(snaps / f"{old_tree}.testmondata")
    _data(snaps / f"{middle_tree}.testmondata")
    assert at.snapshot_for(repo, snaps) == (snaps / f"{middle_tree}.testmondata", middle)


@needs_git
def test_a_snapshot_is_found_by_tree_so_a_squash_merge_still_matches(repo, tmp_path):
    """The gate records the batch's merge tree; the squash merge that follows
    is a different commit with the same tree."""
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    commit, tree = _commit(repo, "a", "1")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "same tree, new commit")
    _data(snaps / f"{tree}.testmondata")
    found, at_commit = at.snapshot_for(repo, snaps)
    assert found.name == f"{tree}.testmondata"
    assert at_commit != commit, "the newest commit with that tree"


@needs_git
def test_a_snapshot_of_no_ancestor_is_not_used(repo, tmp_path):
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    _commit(repo, "a", "1")
    _data(snaps / f"{'0' * 40}.testmondata")
    assert at.snapshot_for(repo, snaps) is None
    assert at.snapshot_for(repo, tmp_path / "no-such-dir") is None


def test_data_is_usable_only_when_it_has_recorded_tests(tmp_path):
    assert at.readable(_data(tmp_path / "good.testmondata", tests=3))
    assert not at.readable(_data(tmp_path / "empty.testmondata", tests=0))
    (tmp_path / "junk.testmondata").write_bytes(b"not a database")
    assert not at.readable(tmp_path / "junk.testmondata")


# --- the fallback: the targeted set by hand -------------------------------------


def test_the_fallback_takes_changed_tests_and_every_test_naming_a_changed_module(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_dotted.py").write_text(
        "from policyforge.org.context import x\n", encoding="utf-8"
    )
    (tests / "test_from.py").write_text("from policyforge.org import context\n", encoding="utf-8")
    (tests / "test_script.py").write_text("import fresh_corpus as fc\n", encoding="utf-8")
    (tests / "test_changed.py").write_text("def test(): pass\n", encoding="utf-8")
    (tests / "test_other.py").write_text("import policyforge.org.roles\n", encoding="utf-8")
    files = [
        "src/policyforge/org/context.py",
        "scripts/fresh_corpus.py",
        "tests/test_changed.py",
        "docs/x.md",
    ]
    assert at.fallback(tmp_path, files) == [
        "tests/test_changed.py",
        "tests/test_dotted.py",
        "tests/test_from.py",
        "tests/test_script.py",
    ]


def test_the_scanners_are_found_and_include_ones_named_by_hand():
    found = at.scanners(ROOT)
    for name in (
        "tests/test_printed_commands_run.py",
        "tests/test_subprocess_encoding.py",
        "tests/test_litellm_stubs_are_complete.py",
    ):
        assert name in found
    assert len(found) >= 40, "premise: the suite has dozens of scanners (49 at #291)"


# --- the run ------------------------------------------------------------------


class Ran:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _usable(monkeypatch, tmp_path, *, chosen, files):
    snap = _data(tmp_path / "t.testmondata")
    monkeypatch.setattr(at, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(at, "changed", lambda root, base: files)
    monkeypatch.setattr(at, "scanners", lambda root: ["tests/test_scan.py"])
    monkeypatch.setattr(at, "snapshot_for", lambda root, snaps: (snap, "c" * 40))
    monkeypatch.setattr(at, "selected", lambda root: chosen)
    ran = Ran()
    monkeypatch.setattr(at.subprocess, "run", ran)
    return ran


def test_an_empty_selection_when_python_changed_is_a_failure(monkeypatch, tmp_path, capsys):
    ran = _usable(monkeypatch, tmp_path, chosen=[], files=["src/policyforge/x.py"])
    assert at.main([]) == 1
    assert ran.calls == [], "nothing runs on an empty selection"
    assert "selects NOTHING" in capsys.readouterr().out


def test_an_empty_selection_with_no_python_changed_runs_the_scanners(monkeypatch, tmp_path):
    ran = _usable(monkeypatch, tmp_path, chosen=[], files=["docs/x.md"])
    _tests(tmp_path, test_doc='"docs/x.md"')
    assert at.main([]) == 0
    (only,) = ran.calls
    assert "tests/test_scan.py" in only and "tests/test_doc.py" in only and "no:testmon" in only


def test_a_selection_runs_under_testmon_and_then_every_scanner(monkeypatch, tmp_path):
    chosen = ["tests/test_a.py::test_one"]
    ran = _usable(monkeypatch, tmp_path, chosen=chosen, files=["src/policyforge/x.py"])
    assert at.main([]) == 0
    first, second = ran.calls
    assert "--testmon" in first
    assert "no:testmon" in second and second[-1] == "tests/test_scan.py"
    assert (tmp_path / at.DATA).exists(), "the snapshot was copied into the worktree"


def test_no_usable_data_falls_back_rather_than_selecting_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(at, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(at, "changed", lambda root, base: ["tests/test_changed.py"])
    monkeypatch.setattr(at, "scanners", lambda root: ["tests/test_scan.py"])
    monkeypatch.setattr(at, "snapshot_for", lambda root, snaps: None)
    monkeypatch.setattr(at, "fallback", lambda root, files: ["tests/test_changed.py"])
    ran = Ran()
    monkeypatch.setattr(at.subprocess, "run", ran)
    assert at.main([]) == 0
    first, _ = ran.calls
    assert first[-1] == "tests/test_changed.py" and "--testmon" not in first
    assert "FALLBACK, no snapshot" in capsys.readouterr().out


# --- the gate records, and still runs everything ------------------------------


def _gate(monkeypatch, argv):
    commands: list[list[str]] = []
    monkeypatch.setattr(check, "run", lambda label, cmd: commands.append(cmd) or True)
    monkeypatch.setattr(check, "check_semgrep", lambda: None)
    monkeypatch.setattr(check, "check_gitleaks", lambda: None)
    recorded: list = []
    monkeypatch.setattr(
        check, "record_selection", lambda snaps, passed: recorded.append((snaps, passed))
    )
    monkeypatch.setattr(
        check.tree_guard,
        "resolved_origin",
        lambda name="policyforge": check.REPO_ROOT / "src" / "policyforge" / "__init__.py",
    )
    check.main(["--allow-skip", "semgrep", "--allow-skip", "gitleaks", *argv])
    (pytest_cmd,) = [c for c in commands if "pytest" in c and "-n" in c]
    return pytest_cmd, recorded


def test_the_gate_records_without_deselecting_only_when_asked(monkeypatch, tmp_path):
    plain, recorded = _gate(monkeypatch, [])
    assert "--testmon-noselect" not in plain and recorded == []
    asked, recorded = _gate(monkeypatch, ["--record-selection", str(tmp_path)])
    assert "--testmon-noselect" in asked and "--testmon" not in asked
    assert recorded and recorded[0][0] == tmp_path


@needs_git
def test_recording_names_the_data_by_tree_and_only_on_a_clean_pass(repo, tmp_path, monkeypatch):
    _, tree = _commit(repo, "a", "1")
    monkeypatch.setattr(check, "REPO_ROOT", repo)
    snaps = tmp_path / "snaps"
    assert not check.record_selection(snaps, passed=True), "no data yet"
    _data(repo / ".testmondata", tests=2)
    assert not check.record_selection(snaps, passed=False)
    (repo / "a").write_text("changed", encoding="utf-8")
    assert not check.record_selection(snaps, passed=True), "the tree isn't HEAD's"
    _git(repo, "checkout", "-q", "--", "a")
    assert check.record_selection(snaps, passed=True)
    assert [p.name for p in snaps.iterdir()] == [f"{tree}.testmondata"]
    assert at.readable(snaps / f"{tree}.testmondata")


def test_recording_keeps_the_newest_ten(tmp_path, monkeypatch):
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    for i in range(12):
        _data(snaps / f"{i:040d}.testmondata")
        stamp = 1_000_000 + i
        os.utime(snaps / f"{i:040d}.testmondata", (stamp, stamp))
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "a", "1")
    _data(repo / ".testmondata")
    monkeypatch.setattr(check, "REPO_ROOT", repo)
    assert check.record_selection(snaps, passed=True)
    kept = sorted(p.name for p in snaps.glob("*.testmondata"))
    assert len(kept) == 10 and f"{0:040d}.testmondata" not in kept


# --- what coverage can't see (ba on PR 303) -------------------------------------


def _tests(root: Path, **files: str) -> None:
    (root / "tests").mkdir(exist_ok=True)
    for name, text in files.items():
        (root / "tests" / f"{name}.py").write_text(text, encoding="utf-8")


def test_a_changed_data_file_adds_the_tests_naming_it(tmp_path):
    _tests(
        tmp_path,
        test_readme='FRAMEWORKS / "arc-ampe" / "README.md"\n',
        test_glob='ROOT / "data" / "frameworks"\n',
        test_other="nothing relevant\n",
    )
    found, unmapped = at.data_tests(
        tmp_path,
        ["data/frameworks/arc-ampe/controls.json", "changelog.d/x.md", "src/policyforge/a.py"],
    )
    assert found == ["tests/test_glob.py", "tests/test_readme.py"]
    assert unmapped == [], "release notes and Python are not data files"


def test_a_changed_file_no_test_names_is_reported(tmp_path):
    _tests(tmp_path, test_a="nothing\n")
    assert at.data_tests(tmp_path, ["config/brand-new.yaml"]) == ([], ["config/brand-new.yaml"])


def test_an_unmapped_data_change_fails_unless_accepted(monkeypatch, tmp_path, capsys):
    ran = _usable(monkeypatch, tmp_path, chosen=[], files=["config/brand-new.yaml"])
    _tests(tmp_path, test_a="nothing\n")
    assert at.main([]) == 1 and ran.calls == []
    assert "config/brand-new.yaml" in capsys.readouterr().out
    assert at.main(["--accept-unmapped", "a scratch file"]) == 0
    assert "UNMAPPED, accepted (a scratch file)" in capsys.readouterr().out


def test_the_tests_naming_a_data_change_run_even_when_testmon_selects_nothing(
    monkeypatch, tmp_path
):
    """The case ba measured: a data-only change, testmon selects 0."""
    ran = _usable(
        monkeypatch, tmp_path, chosen=[], files=["data/frameworks/arc-ampe/controls.json"]
    )
    _tests(tmp_path, test_readme='"arc-ampe"\n')
    assert at.main([]) == 0
    (only,) = ran.calls
    assert "tests/test_readme.py" in only and "no:testmon" in only


def test_the_fallback_follows_one_more_hop_through_a_script(tmp_path):
    (tmp_path / "src" / "policyforge" / "org").mkdir(parents=True)
    (tmp_path / "src" / "policyforge" / "org" / "context.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "fresh_corpus.py").write_text(
        "def f():\n    from policyforge.org.context import x\n", encoding="utf-8"
    )
    _tests(tmp_path, test_fresh="import fresh_corpus as fc\n", test_other="nothing\n")
    assert at.fallback(tmp_path, ["src/policyforge/org/context.py"]) == ["tests/test_fresh.py"]


@needs_git
def test_untracked_files_are_changes_and_a_missing_base_is_named(repo):
    _commit(repo, "a", "1")
    _git(repo, "branch", "base")
    (repo / "new_test.txt").write_text("x", encoding="utf-8")
    assert "new_test.txt" in at.changed(repo, "base")
    with pytest.raises(SystemExit, match="no ref 'nope'"):
        at.changed(repo, "nope")


@needs_git
def test_a_carry_finds_the_tip_snapshot_through_the_merges_second_parent(repo, tmp_path):
    """Issue 311: a carry keeps ag's commit as the first parent and merges the
    tip in as the second, so a first-parent walk never reached the tip."""
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    _commit(repo, "a", "1")
    _git(repo, "branch", "ag")
    tip, tip_tree = _commit(repo, "b", "2")  # the train moves on
    _data(snaps / f"{tip_tree}.testmondata")
    _git(repo, "checkout", "-q", "ag")
    _commit(repo, "c", "3")  # ag's fix, on the old base
    _git(repo, "merge", "-q", "--no-edit", tip)  # the carrier merges the tip in
    assert _git(repo, "rev-parse", "HEAD^1") != tip, "premise: the tip is the second parent"
    found = at.snapshot_for(repo, snaps)
    assert found == (snaps / f"{tip_tree}.testmondata", tip)

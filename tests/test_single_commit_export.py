"""The single-commit export CI runs the suite in (#323).

A repository built here with two commits and a tag stands in for the train,
and a test inside it that reads `HEAD~1` stands in for a test that reads
this repository's history. That test passes in the repository and must fail
in the export. The export is stricter than the public repo, which chains
one commit per release onto a tagged root (ba on PR 358): it is one root
commit with no tag.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import single_commit_export as sce  # noqa: E402

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@localhost",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@localhost",
    "GIT_CONFIG_NOSYSTEM": "1",
}

HISTORY_TEST = """\
import subprocess


def test_reads_the_previous_commit():
    done = subprocess.run(["git", "rev-parse", "HEAD~1"], capture_output=True)
    assert done.returncode == 0, "no HEAD~1 here"
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=GIT_ENV,
        check=True,
    ).stdout.strip()


@pytest.fixture
def train(tmp_path, monkeypatch):
    """Two commits and a tag, the second adding a test that reads HEAD~1."""
    repo = tmp_path / "train"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=release")
    _git(repo, "config", "core.fileMode", "true")
    (repo / "README.md").write_text("first\n", encoding="utf-8")
    (repo / "run.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    os.chmod(repo / "run.sh", 0o755)
    _git(repo, "add", "-A")
    _git(repo, "update-index", "--chmod=+x", "run.sh")
    _git(repo, "commit", "-q", "-m", "first")
    _git(repo, "tag", "v1.0.0")
    (repo / "test_history.py").write_text(HISTORY_TEST, encoding="utf-8")
    # Only the new file: `add -A` would re-stage run.sh from disk, and under
    # core.fileMode=true (Linux) a disk mode without the exec bit made it
    # 100644 in HEAD (b5 on PR 358, CI run 738).
    _git(repo, "add", "test_history.py")
    _git(repo, "commit", "-q", "-m", "second")
    monkeypatch.chdir(repo)
    return repo


def _pytest(where: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_history.py"],
        cwd=where,
        capture_output=True,
        env=GIT_ENV,
        check=False,
    ).returncode


def test_the_history_test_passes_in_the_train(train):
    """The control: in the repository it was written in, it passes."""
    assert _pytest(train) == 0


def test_the_export_is_one_root_commit_with_the_same_tree(train, tmp_path):
    dest = tmp_path / "export"

    root = sce.export(train, dest)

    assert _git(dest, "rev-list", "--all").split() == [root]
    assert _git(dest, "rev-list", "--max-parents=0", "HEAD") == root
    assert _git(dest, "rev-parse", "HEAD^{tree}") == _git(train, "rev-parse", "HEAD^{tree}")
    assert _git(dest, "tag", "--list") == ""
    assert _git(dest, "remote") == ""
    assert not (dest / ".git" / "shallow").exists()
    # The file mode is the tree's, since the tree was fetched rather than
    # rebuilt: the source HEAD's mode, and that mode is the executable one.
    source_mode = _git(train, "ls-tree", "HEAD", "run.sh").split()[0]
    assert source_mode == "100755"
    assert _git(dest, "ls-tree", "HEAD", "run.sh").split()[0] == source_mode


def test_the_source_commit_does_not_survive_in_the_export(train, tmp_path):
    """The fetched commit carries the source's parent: it must be gone, not
    just unreferenced."""
    dest = tmp_path / "export"
    sce.export(train, dest)
    head = _git(train, "rev-parse", "HEAD")

    done = subprocess.run(
        ["git", "-C", str(dest), "cat-file", "-e", head], env=GIT_ENV, check=False
    )
    assert done.returncode != 0


def test_a_test_that_reads_history_fails_in_the_export(train, tmp_path):
    """What the CI job exists for: the history test fails there, through the
    script's own pytest run."""
    dest = tmp_path / "export"

    code = sce.main([str(dest), "--", "-q", "-p", "no:cacheprovider", "test_history.py"])

    assert code != 0


def test_a_clean_export_runs_pytest_and_passes_on(train, tmp_path):
    """What it must ALLOW: a test that needs no history passes in the export."""
    (train / "test_plain.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(train, "add", "test_plain.py")
    _git(train, "commit", "-q", "-m", "third")

    dest = tmp_path / "export"
    code = sce.main([str(dest), "--", "-q", "-p", "no:cacheprovider", "test_plain.py"])

    assert code == 0


def test_a_dest_that_is_not_empty_is_refused(train, tmp_path, capsys):
    dest = tmp_path / "export"
    dest.mkdir()
    (dest / "stray.txt").write_text("x", encoding="utf-8")

    assert sce.main([str(dest)]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_verify_refuses_a_repository_with_history(train):
    """`verify` is what stands between a wrong export and a green CI job, so
    it must refuse the train itself."""
    with pytest.raises(sce.ExportRefused) as refused:
        sce.verify(train, _git(train, "rev-parse", "HEAD^{tree}"))

    assert "2 commits, not 1" in str(refused.value)
    assert "tags" in str(refused.value)


def test_verify_refuses_an_export_with_a_remote(train, tmp_path):
    """A remote would let a test reach history the export doesn't hold (ba's
    arm on PR 358: with this check off, 9 of 9 passed)."""
    dest = tmp_path / "export"
    sce.export(train, dest)
    _git(dest, "remote", "add", "origin", train.as_uri())

    with pytest.raises(sce.ExportRefused, match=r"it has remotes: \['origin'\]"):
        sce.verify(dest, _git(train, "rev-parse", "HEAD^{tree}"))


def test_verify_refuses_an_export_whose_working_tree_differs(train, tmp_path):
    """The suite runs on the working tree, not the commit, so a file that
    differs from it is a test of something else (ba's arm on PR 358)."""
    dest = tmp_path / "export"
    sce.export(train, dest)
    (dest / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(sce.ExportRefused, match="its working tree differs"):
        sce.verify(dest, _git(train, "rev-parse", "HEAD^{tree}"))


def test_export_checks_what_it_built_against_the_source_tree(train, tmp_path, monkeypatch):
    """A correct export passes with or without its checks, so this pins that
    `export` runs them, on what it built, against the source's tree (1d's
    arm on PR 358: `verify` never called, and 7 of 7 still passed)."""
    seen = []
    monkeypatch.setattr(sce, "verify", lambda dest, want: seen.append((dest, want)))
    dest = tmp_path / "export"

    sce.export(train, dest)

    assert seen == [(dest, _git(train, "rev-parse", "HEAD^{tree}"))]


def test_a_refused_export_exits_2(train, tmp_path, monkeypatch, capsys):
    def refuse(dest, want):
        raise sce.ExportRefused("it holds 2 commits, not 1")

    monkeypatch.setattr(sce, "verify", refuse)
    assert sce.main([str(tmp_path / "export"), "--", "-q"]) == 2
    assert "REFUSED: it holds 2 commits, not 1" in capsys.readouterr().err

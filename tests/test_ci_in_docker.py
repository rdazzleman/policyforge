"""`ci_in_docker`'s build context is the tree's bytes, not the checkout's (#171).

`git archive` applies the checkout's `core.autocrlf`, and on this machine that
is `true` system-wide, so the context carried CRLF in 490 of 680 files. The
fixture makes the setting hostile in the repository itself, so the test bites
on any machine, whatever its own git config says (the way #167's does).

Only the context build is exercised, with real git and no mocks: the rest of
`image_checks` needs a Docker engine (`ci_in_docker` itself is not part of the
suite).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ci_in_docker  # noqa: E402

_ID = ["-c", "user.name=policyforge-test", "-c", "user.email=test@invalid"]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *_ID, *args], cwd=cwd, capture_output=True, check=True)


def _hostile_repo(tmp_path: Path) -> Path:
    """One committed LF file, then core.autocrlf=true in the repo itself."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "script.sh").write_bytes(b"#!/bin/sh\necho hello\n")
    _git(repo, "add", "script.sh")
    _git(repo, "commit", "-q", "-m", "LF")
    _git(repo, "config", "core.autocrlf", "true")
    return repo


def test_the_build_context_holds_the_trees_bytes_under_a_hostile_autocrlf(tmp_path):
    context = ci_in_docker.build_context(_work(tmp_path), _hostile_repo(tmp_path))
    assert (context / "script.sh").read_bytes() == b"#!/bin/sh\necho hello\n"


def test_the_hostile_fixture_does_convert_without_the_setting(tmp_path):
    """The premise, so the test above can fail: the same repo, archived
    without `-c core.autocrlf=false`, writes CRLF. Without this, a fixture
    git ignored would pass both ways."""
    repo = _hostile_repo(tmp_path)
    out = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"], cwd=repo, capture_output=True, check=True
    )
    assert b"echo hello\r\n" in out.stdout


def _text_marked_repo(tmp_path: Path) -> Path:
    """b5 on #206: `* text=auto` in .gitattributes and core.eol=crlf in the
    repo. autocrlf off doesn't stop a text-marked path being converted to
    core.eol. Set in the repo, not left native, so the fixture is hostile on
    Linux CI too, where native is LF."""
    repo = tmp_path / "marked"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitattributes").write_bytes(b"* text=auto\n")
    (repo / "script.sh").write_bytes(b"#!/bin/sh\necho hello\n")
    _git(repo, "add", ".gitattributes", "script.sh")
    _git(repo, "commit", "-q", "-m", "LF, text-marked")
    _git(repo, "config", "core.eol", "crlf")
    return repo


def test_the_build_context_holds_the_trees_bytes_for_a_text_marked_path(tmp_path):
    context = ci_in_docker.build_context(_work(tmp_path), _text_marked_repo(tmp_path))
    assert (context / "script.sh").read_bytes() == b"#!/bin/sh\necho hello\n"


def test_autocrlf_off_alone_still_converts_a_text_marked_path(tmp_path):
    """The premise for the test above: `-c core.autocrlf=false` alone, the
    first fix, still writes CRLF for a text-marked path."""
    repo = _text_marked_repo(tmp_path)
    out = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "archive", "--format=tar", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    assert b"echo hello\r\n" in out.stdout


def test_the_decoys_are_planted_in_the_context(tmp_path):
    """The checks after the build look for these: the refactor must keep them."""
    context = ci_in_docker.build_context(_work(tmp_path), _hostile_repo(tmp_path))
    for decoy in ci_in_docker.PRIVATE_DECOYS:
        assert (context / decoy).is_file(), decoy


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


def test_uv_version_is_not_vulnerable():
    assert ci_in_docker.UV_VERSION == "0.12.18"

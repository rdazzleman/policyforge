"""What gitleaks scans, locally and in CI: the history of the commit under
test, the same scope on both sides (#30).

Before, `check.py` scanned every local ref, and in the shared clone that is
every session's branches: one flagged unpushed commit turned every gate red.
CI had the same defect one level up, where `fetch-depth: 0` fetches every
branch on the server, and a secret planted on a throwaway branch turned an
unrelated PR's run red (#30, run 127). Both now pass `--log-opts=HEAD`.

The synthetic token below is assembled at run time and never written out
whole, so this file is not itself something the repository's scan flags.
"""

from __future__ import annotations

import secrets
import shlex
import shutil
import string
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402


def _ci_detect_args() -> list[str]:
    """The arguments of CI's `gitleaks detect` line, read from ci.yml itself:
    the other side of the check, not check.py's constant."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    steps = workflow["jobs"]["scan-and-test"]["steps"]
    (step,) = [s for s in steps if "gitleaks" in s.get("name", "")]
    (line,) = [ln for ln in step["run"].splitlines() if " detect " in f" {ln} "]
    words = shlex.split(line)
    return words[words.index("detect") :]


def _without_source(args) -> set[str]:
    """The flags, with `--source <path>` removed: the path is where each side
    runs from, not what it scans."""
    args = list(args)
    if "--source" in args:
        at = args.index("--source")
        del args[at : at + 2]
    return set(args)


@pytest.mark.github_config
def test_ci_and_check_py_scan_the_same_scope():
    ci = _without_source(_ci_detect_args())
    local = _without_source(check.GITLEAKS_ARGS)
    assert ci == local, f"CI scans with {sorted(ci)}, check.py with {sorted(local)}"


@pytest.mark.github_config
def test_both_scan_only_the_commit_under_test():
    assert "--log-opts=HEAD" in _ci_detect_args()
    assert "--log-opts=HEAD" in check.GITLEAKS_ARGS


def _token() -> str:
    """A GitHub-token-shaped synthetic string, built at run time."""
    alphabet = string.ascii_letters + string.digits
    return "gh" + "p_" + "".join(secrets.choice(alphabet) for _ in range(36))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _scan(repo: Path) -> int:
    """check.py's real arguments against a scratch repository: the exit code."""
    return subprocess.run(
        ["gitleaks", *check.GITLEAKS_ARGS, "--source", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).returncode


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks binary not on PATH")
def test_a_secret_on_another_branch_does_not_fail_the_scan_but_one_in_head_does(tmp_path):
    """The two halves 80 and 9b asked for, through the real binary (#30)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "readme.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "readme.txt")
    _git(repo, "commit", "-q", "-m", "clean")

    _git(repo, "switch", "-q", "-c", "other")
    (repo / "other.py").write_text(f'TOKEN = "{_token()}"\n', encoding="utf-8")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-q", "-m", "a secret on another branch")
    _git(repo, "switch", "-q", "main")

    assert _scan(repo) == 0, "another branch's secret failed the scan of main"

    (repo / "head.py").write_text(f'TOKEN = "{_token()}"\n', encoding="utf-8")
    _git(repo, "add", "head.py")
    _git(repo, "commit", "-q", "-m", "a secret in HEAD's history")
    assert _scan(repo) == 1, "a secret in HEAD's own history was not found"

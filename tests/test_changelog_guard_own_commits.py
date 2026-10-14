"""`changelog_guard` counts what the branch's OWN commits changed (#197).

Real repositories, not mocks: the defect lived in what `git diff base...HEAD`
returns once a train is merged into a branch and `base` is older than that
merge. The world, every case:

    B ---------------- T          train: a fragment, and a user-facing file
     \\                  \\
      F1 (own work) ---- M         the branch: its own commit, then the train merged in

`B` is the stale base; `T` is the fresh one. Before #197 both of 9b's and
ba's cases went wrong against `B` and right against `T`, so the answer
depended on how fresh the caller's ref was.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import changelog_guard as guard  # noqa: E402

_ID = ["-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "core.autocrlf=false"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *_ID, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()


def _write(repo: Path, path: str, text: str = "x\n") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", path)


def _world(tmp_path: Path, own: dict[str, str]) -> tuple[Path, str, str]:
    """B, a train T (a fragment plus a user-facing file), and a branch whose
    own commit writes `own`, with the train merged in on top. Returns
    (repo, B, T)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "README.md")
    _git(repo, "commit", "-q", "-m", "B")
    stale = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "train")
    _write(repo, "changelog.d/someone-else.md", "**Someone else's change.**\n")
    _write(repo, "src/policyforge/cli/theirs.py")
    _git(repo, "commit", "-q", "-m", "T: another PR, landed on the train")
    fresh = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature", stale)
    for path, text in own.items():
        _write(repo, path, text)
    _git(repo, "commit", "-q", "-m", "F1: the branch's own work")
    _git(repo, "merge", "-q", "--no-edit", "train")
    return repo, stale, fresh


def _verdict(repo: Path, base: str) -> bool:
    ok, _ = guard.decide(guard.changed_paths(base, repo), "")
    return ok


def test_a_merged_in_train_fragment_does_not_stand_for_the_branch(tmp_path):
    """9b on #197, measured: against the stale base, the train's fragment
    was in `base...HEAD` and passed a branch that has none of its own."""
    repo, stale, fresh = _world(tmp_path, {"src/policyforge/cli/mine.py": "y\n"})
    assert not _verdict(repo, stale)
    assert not _verdict(repo, fresh), "the fresh base agreed before #197 too"


def test_the_branchs_own_fragment_still_counts_after_a_train_merge(tmp_path):
    repo, stale, fresh = _world(
        tmp_path,
        {"src/policyforge/cli/mine.py": "y\n", "changelog.d/mine.md": "**Mine.**\n"},
    )
    assert _verdict(repo, stale) and _verdict(repo, fresh)


def test_a_tests_only_branch_does_not_inherit_the_trains_user_facing_paths(tmp_path):
    """ba on #197: against the stale base, the train's user-facing path was
    in `base...HEAD`, so a tests-only branch failed for someone else's
    change: a false red, the kind people learn to mute."""
    repo, stale, fresh = _world(tmp_path, {"tests/test_mine.py": "def test(): pass\n"})
    assert _verdict(repo, stale)
    assert _verdict(repo, fresh)
    assert guard.changed_paths(stale, repo) == ["tests/test_mine.py"]


def test_a_fragment_added_only_in_a_merge_resolution_does_not_count(tmp_path):
    """The stated cost of the first-parent rule: a merge commit isn't the
    branch's own commit, so a fragment that exists only there doesn't
    count. Add it in a commit of its own."""
    repo, stale, _ = _world(tmp_path, {"src/policyforge/cli/mine.py": "y\n"})
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _git(repo, "merge", "-q", "--no-commit", "train")
    _write(repo, "changelog.d/mine.md", "**Mine.**\n")
    _git(repo, "commit", "-q", "-m", "merge train, plus a fragment in the merge")
    assert "changelog.d/mine.md" not in guard.changed_paths(stale, repo)
    assert not _verdict(repo, stale)


def test_a_path_the_branch_touched_and_put_back_does_not_count(tmp_path):
    """Intersected with the net diff: own commits that cancel out leave
    nothing to describe."""
    repo, stale, _ = _world(tmp_path, {"tests/test_mine.py": "def test(): pass\n"})
    _write(repo, "src/policyforge/cli/tmp.py")
    _git(repo, "commit", "-q", "-m", "add")
    _git(repo, "rm", "-q", "src/policyforge/cli/tmp.py")
    _git(repo, "commit", "-q", "-m", "and take it back")
    assert "src/policyforge/cli/tmp.py" not in guard.changed_paths(stale, repo)
    assert _verdict(repo, stale)


def _merge_ref(tmp_path: Path, own: dict[str, str]) -> tuple[Path, str]:
    """GitHub's `pull_request` checkout: refs/pull/N/merge, a merge whose
    FIRST parent is the base tip and whose second is the PR head."""
    repo = tmp_path / "gh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "README.md")
    _git(repo, "commit", "-q", "-m", "B")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "pr")
    for path, text in own.items():
        _write(repo, path, text)
    _git(repo, "commit", "-q", "-m", "the PR's own work")
    _git(repo, "checkout", "-q", "--detach", base)
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "pr")
    return repo, base


def test_githubs_merge_ref_checkout_still_sees_the_prs_commits(tmp_path):
    """ba on #211, measured: on refs/pull/N/merge the first-parent walk from
    HEAD found nothing, so a user-facing change with no fragment passed. The
    walk now starts from HEAD^2 when HEAD is a merge onto the base."""
    repo, base = _merge_ref(tmp_path, {"src/policyforge/cli/mine.py": "y\n"})
    assert guard.changed_paths(base, repo) == ["src/policyforge/cli/mine.py"]
    assert not _verdict(repo, base)
    (tmp_path / "with").mkdir()
    repo2, base2 = _merge_ref(
        tmp_path / "with", {"src/policyforge/cli/mine.py": "y\n", "changelog.d/mine.md": "**M.**\n"}
    )
    assert _verdict(repo2, base2)


def test_no_own_commit_with_user_facing_net_changes_fails_closed(tmp_path, monkeypatch, capsys):
    """ba on #211: if the own-commit walk finds nothing while the net diff
    changes what a user sees, the walk saw the wrong line, and the guard
    refuses rather than pass on nothing. The shape: a branch that is only
    merges (B, then M1, then M2), so HEAD's first parent isn't the base and
    the merge-ref rule doesn't apply."""
    repo = tmp_path / "only-merges"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "README.md")
    _git(repo, "commit", "-q", "-m", "B")
    base = _git(repo, "rev-parse", "HEAD")
    for side in ("one", "two"):
        _git(repo, "checkout", "-q", "-b", side, base)
        _write(repo, f"src/policyforge/cli/{side}.py")
        _git(repo, "commit", "-q", "-m", side)
    _git(repo, "checkout", "-q", "-b", "merges-only", base)
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "one")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "two")
    assert guard.changed_paths(base, repo) == [], "the premise: no own commit"
    assert guard.unattributed(base, repo) == [
        "src/policyforge/cli/one.py",
        "src/policyforge/cli/two.py",
    ]
    monkeypatch.chdir(repo)
    assert guard.main(["--base", base]) == 1
    assert "No commit of this branch's own was found" in capsys.readouterr().out


def test_a_branch_with_its_own_commits_is_not_called_unattributed(tmp_path):
    """The passing case for the refusal: it speaks only when the walk found
    nothing at all."""
    repo, stale, _ = _world(tmp_path, {"tests/test_mine.py": "def test(): pass\n"})
    assert guard.unattributed(stale, repo) == []

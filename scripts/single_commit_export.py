"""Export HEAD as a repository of ONE root commit, and optionally run pytest there (#323).

    python scripts/single_commit_export.py DEST [-- PYTEST_ARGS...]

WHY. The public repository holds one commit per minor release, each
chained onto the last, and none of the train's history (#115). ba measured
it on PR 358: its main is the root `05da4bb2`, tagged `v1.7.0`. A test that
reads this repository's own history (`HEAD~1`, a tag, a merge-base) passes
on the train, in every local gate and in CI, and can fail after
publication, on the public repo's CI (164). This builds a STRICTER shape:
one root commit and no tags, which is what the public repo held at its
first release, before its tag. A test that needs any history or tag fails
here, so it fails in review rather than after a publish.

WHAT IT BUILDS. A fresh repository at DEST holding exactly one commit, a
root, whose tree is HEAD's tree. The tree is fetched, not re-created from
files: `git fetch --depth=1` brings HEAD's tree into DEST, `commit-tree`
records it as a root commit, and the fetched commit is then deleted (the
shallow marker, FETCH_HEAD and its objects). So line endings, file modes
and `.gitattributes` export rules can't change a byte, which an archive
would. Then it CHECKS, and refuses (exit 2) unless all of these hold:

- DEST's tree is the source HEAD's tree, by hash;
- DEST has exactly one commit on any ref, and it has no parent;
- DEST has no tags and no remotes;
- DEST's working tree is clean against that commit.

With `-- PYTEST_ARGS`, it then runs `python -m pytest PYTEST_ARGS` in DEST
and exits with pytest's code. Which `policyforge` that imports is the
caller's to arrange: CI installs DEST with `pip install --no-deps -e`.

EXIT: 0 the export holds (and pytest passed, if run); pytest's own code if
it ran and failed; 2 the export isn't a faithful single commit, or DEST
isn't empty.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

#: Fixed so that two exports of one tree are the same commit, and so no
#: identity of whoever runs it is written into the export.
IDENTITY = {
    "GIT_AUTHOR_NAME": "single-commit-export",
    "GIT_AUTHOR_EMAIL": "export@localhost",
    "GIT_COMMITTER_NAME": "single-commit-export",
    "GIT_COMMITTER_EMAIL": "export@localhost",
    "GIT_AUTHOR_DATE": "1970-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "1970-01-01T00:00:00Z",
}
BRANCH = "export"


class ExportRefused(RuntimeError):
    """The export isn't the single-commit shape it claims to be."""


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    done = subprocess.run(
        # core.autocrlf=false on every call: Git for Windows' system config
        # sets it true, and a checkout under it would write CRLF the tree
        # doesn't have (measured on PR 357's archive runs).
        ["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if done.returncode != 0:
        raise ExportRefused(f"git {' '.join(args)} failed in {repo}: {done.stderr.strip()}")
    return done.stdout.strip()


def export(source: Path, dest: Path) -> str:
    """Build the single-commit repository at `dest`; return its commit."""
    if dest.exists() and any(dest.iterdir()):
        raise ExportRefused(f"{dest} exists and isn't empty")
    want = _git(source, "rev-parse", "HEAD^{tree}")
    head = _git(source, "rev-parse", "HEAD")
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q", f"--initial-branch={BRANCH}")
    _git(dest, "config", "core.autocrlf", "false")
    _git(dest, "config", "core.logAllRefUpdates", "false")
    # file:// so --depth is honoured (a plain local path ignores it).
    _git(dest, "fetch", "-q", "--no-tags", "--depth=1", source.resolve().as_uri(), head)
    tree = _git(dest, "rev-parse", "FETCH_HEAD^{tree}")
    env = {**os.environ, **IDENTITY}
    root = _git(dest, "commit-tree", tree, "-m", f"single-commit export of {head}", env=env)
    _git(dest, "update-ref", f"refs/heads/{BRANCH}", root)
    # Forget the fetched commit: it carries the source's parent, which is the
    # history this export exists to be without.
    for leftover in ("shallow", "FETCH_HEAD"):
        (dest / ".git" / leftover).unlink(missing_ok=True)
    _git(dest, "reflog", "expire", "--expire=now", "--all")
    _git(dest, "gc", "-q", "--prune=now")
    _git(dest, "checkout", "-q", "-f", BRANCH)
    verify(dest, want)
    return root


def verify(dest: Path, want_tree: str) -> None:
    """Refuse unless `dest` is one root commit whose tree is `want_tree`."""
    problems = []
    tree = _git(dest, "rev-parse", "HEAD^{tree}")
    if tree != want_tree:
        problems.append(f"its tree is {tree}, not the source's {want_tree}")
    commits = _git(dest, "rev-list", "--all").split()
    if len(commits) != 1:
        problems.append(f"it holds {len(commits)} commits, not 1")
    if _git(dest, "rev-list", "--max-parents=0", "HEAD").split() != commits[:1]:
        problems.append("its commit isn't a root")
    tags = _git(dest, "tag", "--list")
    if tags:
        problems.append(f"it has tags: {tags.split()}")
    remotes = _git(dest, "remote")
    if remotes:
        problems.append(f"it has remotes: {remotes.split()}")
    dirty = _git(dest, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        problems.append(f"its working tree differs from the commit: {dirty.splitlines()[:5]}")
    if problems:
        raise ExportRefused("; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dest", type=Path, help="an empty or absent directory to build it in")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="after --, run pytest there")
    args = parser.parse_args(argv)
    source = Path.cwd()
    try:
        root = export(source, args.dest)
    except ExportRefused as exc:
        print(f"single_commit_export: REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"single_commit_export: {args.dest} holds one root commit {root}, HEAD's tree")
    pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    if not pytest_args:
        return 0
    return subprocess.run(
        [sys.executable, "-m", "pytest", *pytest_args], cwd=args.dest, check=False
    ).returncode


if __name__ == "__main__":
    sys.exit(main())

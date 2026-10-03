"""Build the public commit P from this tree and run the publish guard on it, BEFORE the cut (#49).

    python scripts/public_rehearsal.py [--cut REF] [--base REF] [--worktree]

WHY. The first time the publish guard (#35) ever ran on a real P was going
to be the release itself. ba built P by hand on #50's head and found the
guard refusing it on one docstring line in `release.py` (#49): a defect that
existed for a day, invisible, because nothing built P until the cut would.
This builds P the way `release.py`'s `build_public` does, and guards it, on
every gate run, so the cut is never the first rehearsal.

WHAT IT DOES, step for step with `build_public`, minus the network:
- L, the paths P drops, comes from `release.licensed_set` itself, so this and
  the release cannot disagree about what leaves. A public file that still
  references a licensed module is REFUSED there, and here: P can't be built.
- G, the commit P is built on, is the newest release tag (`vX.Y.Z`) instead
  of GitHub `main`: nobody touches GitHub, and a tag is what names each
  published P. `--base` overrides it.
- P is the cut's tree minus L, committed on G, and `publish_guard.guard`
  checks P over G with the cut as the source history.

WHICH TREE. CI rehearses the commit it checked out (`--cut HEAD`, the
default). `check.py` passes `--worktree`: it runs BEFORE a commit, so HEAD
alone would rehearse the parent and never the change being gated. With it,
the cut is HEAD plus every tracked edit, deletion and new not-ignored file,
as `git add -A` would stage them, built in a scratch index.

NOTHING IS LEFT BEHIND. Every object this writes (the index's tree, P's
commit) goes to a temporary object directory, with the repository's own
objects as alternates, and is deleted with it. The shared clone gains no
loose object and no ref, and nothing is pushed. Paths reach git as an argv
list, never through a text file: a CRLF path list once built an EMPTY P
(the guard answered "nothing to check", exit 2, on 1d's hand-built run).

EXIT: 0 P built and the guard passed; 1 P could not be built (a public file
references a licensed module) or the guard refused it, each finding named;
2 the rehearsal itself could not run (no release tag to build on, a git
step failed, or the guard found nothing to check). A rehearsal that couldn't
run is never a pass, and never reads as a refusal either: an uncaught error
would exit 1, which is the verdict code, so every failure to run is caught
and reported as 2.

WHAT IT CANNOT SEE: GitHub's real `main`. If GitHub moved past the newest
tag, the cut's G differs from this one; the guard's verdict on P's own
content doesn't depend on G, but the range it scans does.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The guard and the release's own derivation, imported INSIDE a handler: an
# ImportError at module level exited 1, the refusal code, before main() had
# a handler to catch it (b5 on #58). A failure here is recorded and reported
# by main() as exit 2, like every other failure to run.
try:
    import publish_guard
    import release
except Exception as _exc:  # noqa: BLE001 -- reported by main() as exit 2
    _IMPORT_ERROR: Exception | None = _exc
    publish_guard = release = None  # type: ignore[assignment]
else:
    _IMPORT_ERROR = None

REPO_ROOT = Path(__file__).resolve().parent.parent
_RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class NothingToRehearse(RuntimeError):
    """No release tag to build P on: exit 2, never a pass."""


def _git(*args: str, root: Path, env: dict | None = None, stdin: str | None = None) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def newest_release_tag(root: Path) -> str:
    """The newest `vX.Y.Z` tag by version, as a commit id."""
    tags = [t for t in _git("tag", "--list", "v*", root=root).splitlines() if _RELEASE_TAG.match(t)]
    if not tags:
        raise NothingToRehearse("no vX.Y.Z release tag here to build P on; pass --base")
    newest = max(tags, key=lambda t: tuple(int(n) for n in _RELEASE_TAG.match(t).groups()))
    return _git("rev-parse", f"{newest}^{{commit}}", root=root)


def licensed_paths(cut: str, root: Path) -> tuple[list[str], dict[str, str], dict]:
    """(L, refused, classes) from `release.licensed_set`, the release's own derivation."""

    def run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )

    ctx = release.Context(version="0.0.0", root=root, run=run, forge="forgejo")
    return release.licensed_set(ctx, cut)


def rehearse(cut: str, base: str, root: Path, out=print, worktree: bool = False) -> int:
    """Build P from `cut` (or, with `worktree`, from HEAD plus the working
    tree's changes: what the next commit would publish) and guard it."""
    scratch = Path(tempfile.mkdtemp(prefix="public-rehearsal-"))
    common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir", root=root))
    (scratch / "objects").mkdir()
    # Every git call in this run, ours and the guard's and licensed_set's,
    # writes new objects to the scratch store and reads the repository's
    # through the alternates. Restored in `finally`.
    store = {
        "GIT_OBJECT_DIRECTORY": str(scratch / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(common / "objects"),
    }
    saved = {k: os.environ.get(k) for k in store}
    os.environ.update(store)
    try:
        env = {
            **{k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")},
            **store,
            # P's commit identity and date are fixed, so the same cut and G give
            # the same P, and a session's exported identity (#45) can't change it.
            "GIT_AUTHOR_NAME": "public-rehearsal",
            "GIT_AUTHOR_EMAIL": "public-rehearsal@invalid",
            "GIT_COMMITTER_NAME": "public-rehearsal",
            "GIT_COMMITTER_EMAIL": "public-rehearsal@invalid",
        }
        when = _git("show", "-s", "--format=%cI", cut, root=root)
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
        source = cut
        if worktree:
            # The gate runs BEFORE a commit: HEAD alone would rehearse the
            # parent, never the change being gated. Tracked edits, deletions
            # and new not-ignored files, as `git add -A` would stage them.
            wt = {**env, "GIT_INDEX_FILE": str(scratch / "worktree-index")}
            _git("read-tree", cut, root=root, env=wt)
            _git("add", "-A", root=root, env=wt)
            cut = _git("write-tree", root=root, env=wt)
            out(f"rehearsing the WORKING TREE (HEAD {source[:12]} plus uncommitted changes)")
        removed, refused, classes = licensed_paths(cut, root)
        out(f"cut {cut[:12]}; G {base[:12]}")
        out(f"licensed set L ({len(removed)}): {', '.join(removed) or 'none'}")
        out(
            "files naming a licensed module, by class: "
            + ", ".join(f"{kind} {len(paths)}" for kind, paths in classes.items())
        )
        if refused:
            out("P CANNOT BE BUILT: public code still references a licensed module:")
            for path, kind in refused.items():
                out(f"  {path} ({kind})")
            return 1
        index = {**env, "GIT_INDEX_FILE": str(scratch / "index")}
        _git("read-tree", cut, root=root, env=index)
        if removed:
            # Index only. `rm --cached` compares with the WORKTREE's HEAD and
            # refused a cut other than the checkout (1d's control arm at
            # 6b61bc9); build_public avoids that with a worktree AT the cut.
            _git("update-index", "--force-remove", "--", *removed, root=root, env=index)
        tree = _git("write-tree", root=root, env=index)
        public = _git(
            "commit-tree", tree, "-p", base, root=root, env=index, stdin="public rehearsal\n"
        )
        out(f"P {public[:12]} built in a temporary object store (no ref, nothing pushed)")
        still = [p for p in removed if _git("ls-tree", "--name-only", public, "--", p, root=root)]
        if still:
            out(f"P STILL CARRIES L: {', '.join(still)}")
            return 1
        try:
            findings = publish_guard.guard(public, base, source, root)
        except publish_guard.NothingToCheck as exc:
            out(f"publish_guard: NOTHING TO CHECK: {exc}")
            return 2
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(scratch, ignore_errors=True)
    if findings:
        out(f"publish_guard REFUSED P, {len(findings)} finding(s):")
        for f in findings:
            out(f"  {f.layer}  {f.where}: {f.why}")
        return 1
    out("publish_guard: P is clean (no licensed parser, blob, import or envelope)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cut", default="HEAD", help="the commit to build P from (default HEAD)")
    parser.add_argument("--base", help="G, the commit P is built on (default: newest vX.Y.Z tag)")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="rehearse HEAD plus uncommitted changes (what check.py uses: it runs before a commit)",
    )
    args = parser.parse_args(argv)
    if _IMPORT_ERROR is not None:
        print(
            "public_rehearsal: COULD NOT REHEARSE: the guard or the release could not be "
            f"imported: {type(_IMPORT_ERROR).__name__}: {_IMPORT_ERROR}",
            file=sys.stderr,
        )
        return 2
    try:
        cut = _git("rev-parse", f"{args.cut}^{{commit}}", root=REPO_ROOT)
        base = (
            _git("rev-parse", f"{args.base}^{{commit}}", root=REPO_ROOT)
            if args.base
            else newest_release_tag(REPO_ROOT)
        )
    except Exception as exc:  # noqa: BLE001 -- any failure to start is exit 2, never a verdict
        print(
            f"public_rehearsal: NOTHING TO REHEARSE: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return 2
    try:
        return rehearse(cut, base, REPO_ROOT, worktree=args.worktree)
    except Exception as exc:  # noqa: BLE001 -- deliberate: see below
        # EVERY exception, not only RuntimeError: the guard's own `_read_blobs`
        # raises CalledProcessError, and one that escaped exited 1, the refusal
        # code (9b on #58). A failure to run is reported as 2, with its type,
        # so a crash can never read as a verdict.
        print(f"public_rehearsal: COULD NOT REHEARSE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Rehearse the release cut on HEAD, then run the tests there (#120).

    python scripts/cut_rehearsal.py --version 1.8.0           the whole suite
    python scripts/cut_rehearsal.py --version 1.8.0 tests/x.py  only these

A test that reads a changelog fragment by name passes on every train gate,
because the fragments exist until the cut, and fails on the first tree where
they're gone: the release PR. #118 was that, found with the user waiting. So
this builds that tree before the cut does and runs the tests on it.

**The cut's own code, not a copy of it.** In a throwaway worktree of HEAD,
this tree's `scripts/release.py` runs its `changelog`, `version` and
`install-pins` steps exactly as the cut would: each step's gate, its action,
then its postcondition. The result is committed, so `git ls-files` sees what
a release commit would, and pytest runs there with that tree's `src/` first.

**Wired into the cut:** `release.py`'s `changelog` step runs it as part of
its gate, so a cut can't assemble the fragments for a tree whose tests fail
once assembled. A pass is remembered for its tree and version
(`output/.check/cut-rehearsal/`), so a dry run, a re-run after a WAIT, or a
second operator doesn't run the suite again on the same tree.

**Opt-in otherwise.** It is a second full run of the suite, about five
minutes, so it isn't in `check.py`'s every-gate list.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Where a pass is remembered, as `<tree>-<version>.ok`.
PASSES = REPO_ROOT / "output" / ".check" / "cut-rehearsal"

#: Run inside the throwaway worktree, with its own scripts/ first on the path:
#: the three steps of release.py that write the release commit, in its order.
_CUT = """
import subprocess, sys
from pathlib import Path
import release

ctx = release.Context(
    version=sys.argv[1],
    root=Path.cwd(),
    run=lambda argv: subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace"
    ),
)
for name, gate, act, post in (
    ("changelog", release._fragments_ready, release._assemble, release._changelog_cut),
    ("version", release._bump_ready, release._bump, release._bumped),
    ("install-pins", release._pins_ready, release._repin, release._pins_current),
):
    check = gate(ctx)
    if not check.ok:
        sys.exit(f"cut step {name}: its gate fails: " + "; ".join(check.measured))
    act(ctx)
    check = post(ctx)
    if not check.ok:
        sys.exit(f"cut step {name}: its postcondition fails: " + "; ".join(check.measured))
    print(f"cut step {name}: done")
"""

_IDENTITY = {
    "GIT_AUTHOR_NAME": "cut-rehearsal",
    "GIT_AUTHOR_EMAIL": "cut-rehearsal@localhost",
    "GIT_COMMITTER_NAME": "cut-rehearsal",
    "GIT_COMMITTER_EMAIL": "cut-rehearsal@localhost",
}


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs
    )


def rehearse(
    version: str,
    tests: list[str] | None = None,
    *,
    root: Path = REPO_ROOT,
    passes: Path | None = None,
    out=print,
) -> bool:
    """Cut HEAD to `version` in a throwaway worktree and run `tests` (all of
    `tests/` by default) there. True on a pass, which is remembered for the
    tree and version when the whole suite ran."""
    passes = PASSES if passes is None else passes
    tree = _run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True).stdout.strip()
    marker = passes / f"{tree}-{version}.ok"
    if not tests and marker.exists():
        out(f"cut rehearsal: passed before for tree {tree[:12]} at {version}; not run again")
        return True
    work = Path(tempfile.mkdtemp(prefix="cut-rehearsal-"))
    added = _run(["git", "-C", str(root), "worktree", "add", "--detach", str(work), "HEAD"])
    if added.returncode != 0:
        out(f"cut rehearsal: no worktree: {added.stderr.strip()[-300:]}")
        return False
    ok = False
    try:
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(work / "scripts"), str(work)])}
        cut = _run([sys.executable, "-c", _CUT, version], cwd=work, env=env)
        out((cut.stdout + cut.stderr).strip())
        if cut.returncode != 0:
            out("cut rehearsal: FAILED before any test ran: the cut itself didn't hold")
            return False
        _run(["git", "-C", str(work), "add", "-A"], check=True)
        _run(
            ["git", "-C", str(work), "commit", "-q", "-m", f"cut rehearsal {version}"],
            check=True,
            env={**os.environ, **_IDENTITY},
        )
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(work / "src"), str(work)])}
        args = [sys.executable, "-m", "pytest", "-q", "-rs", "-n", "auto", "-p", "no:cacheprovider"]
        ran = subprocess.run([*args, *(tests or ["tests/"])], cwd=work, env=env)
        ok = ran.returncode == 0
    finally:
        removed = _run(["git", "-C", str(root), "worktree", "remove", "--force", str(work)])
        if removed.returncode != 0:
            out(f"cut rehearsal: left {work}: {removed.stderr.strip()[-200:]}")
        elif work.is_dir() and not any(work.iterdir()):
            work.rmdir()  # ours, from mkdtemp, and empty: nothing else is ever removed
    if ok and not tests:
        passes.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{version}\n", encoding="utf-8")
    out(f"cut rehearsal: {'passed' if ok else 'FAILED'} for tree {tree[:12]} cut to {version}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", required=True, help="the version the cut would release")
    parser.add_argument("tests", nargs="*", help="test paths; the whole suite by default")
    args = parser.parse_args(argv)
    return 0 if rehearse(args.version, args.tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())

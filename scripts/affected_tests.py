#!/usr/bin/env python3
"""Run the tests a change affects, for carriers and readers (#291).

    python scripts/affected_tests.py               the affected tests, then the scanners
    python scripts/affected_tests.py --list        only say what would run

The testing-by-role rule asks a carrier or reader for the tests a change
touches, the tests that reach the changed modules, every codebase scanner,
and the must-fail arm. This makes the first three mechanical.

**Which tests reach the changed code** comes from pytest-testmon's data,
recorded by the batch gate's full run on the tree that became the tip
(`check.py --record-selection`, 80's ruling (a) on #291). That data is a
snapshot outside git, named by the tree it was recorded on, because squash
merges give the tip a new commit but the same tree. This script copies the
newest snapshot whose tree is an ancestor's of HEAD into this worktree and
lets testmon select. Data older than the branch only selects more: a file
changed since then changes the fingerprints of the tests that ran it, and a
test the data has never seen is always selected (1d measured both on #291:
five merged fixes replayed, every failing test selected).

**testmon sees only Python.** It fingerprints code through coverage, so a
changed catalog, lock, README or config selects nothing, though tests read
them (ba and b5 on PR 303 each measured a data change that breaks a test
selecting none). So every changed file that isn't Python adds the tests
naming its path, its file name or a directory under the top level, run
beside the scanners. A changed file no test names fails the run, unless
`--accept-unmapped REASON` says why.

**No usable data never means an empty selection.** Missing, unreadable, or
recorded on no ancestor of HEAD: it falls back to the targeted set the rule
describes by hand (the test files the branch changes, plus every test file
that names a changed module), and says so. And a testmon run that selects
nothing while the branch changes Python is a failure, not a pass: testmon
itself exits 0 on it (measured).

**The scanners always run**, in a second invocation testmon can't deselect:
they read files, which coverage doesn't see.

The gate and CI still run the full suite; this only changes what carriers
and readers run. Name what it printed in your verdict.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = Path(r"C:\policyforge-local\testmon")
DATA = ".testmondata"
#: How many ancestors, newest first, are searched for a snapshot.
DEPTH = 300
#: A codebase scanner reads the tree's files rather than importing what it
#: checks, so import- or coverage-based selection can't see it.
_SCANNER = re.compile(r"ast\.parse|ast\.walk|rglob\(|ls-files")


def _git(*args: str, root: Path) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return done.stdout


def scanners(root: Path) -> list[str]:
    """Every test file that scans the codebase, sorted."""
    found = sorted(
        f"tests/{path.name}"
        for path in (root / "tests").glob("test_*.py")
        if _SCANNER.search(path.read_text(encoding="utf-8", errors="replace"))
    )
    if not found:
        raise SystemExit(
            "affected_tests: no scanner found under tests/; the pattern stopped matching"
        )
    return found


def snapshot_for(root: Path, snapshots: Path, depth: int = DEPTH) -> tuple[Path, str] | None:
    """The snapshot recorded on the newest ancestor's tree, and that
    ancestor's commit, or None.

    Every ancestor, not only first parents (issue 311): a carry branch keeps
    ag's commit as its first parent and merges the tip in as the second, so a
    first-parent walk followed ag's old side and never reached the tip's
    snapshot. `git log` lists ancestors newest first by commit date, so the
    first match is the newest snapshot on either side of a merge."""
    if not snapshots.is_dir():
        return None
    have = {path.name[: -len(DATA)]: path for path in snapshots.glob(f"*{DATA}")}
    if not have:
        return None
    lines = _git("log", f"-{depth}", "--format=%H %T", "HEAD", root=root)
    for line in lines.splitlines():
        commit, tree = line.split()
        if tree in have:
            return have[tree], commit
    return None


def readable(path: Path) -> bool:
    """Whether `path` is a testmon database with recorded tests in it."""
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return db.execute("select count(*) from test_execution").fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        db.close()


def changed(root: Path, base: str) -> list[str]:
    """Files this branch changes against its merge-base with `base`, plus any
    uncommitted change and any untracked file (a new test not yet added is
    still a change; ba on PR 303)."""
    try:
        _git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", root=root)
    except subprocess.CalledProcessError:
        raise SystemExit(
            f"affected_tests: no ref {base!r} here. Fetch the train, or pass --base <ref>."
        ) from None
    point = _git("merge-base", base, "HEAD", root=root).strip()
    names = set(_git("diff", "--name-only", point, root=root).split())
    names |= set(_git("ls-files", "--others", "--exclude-standard", root=root).split())
    return sorted(names)


#: Changed files no test is expected to name: release notes and this
#: repository's own housekeeping.
_UNTESTED = re.compile(r"^(changelog\.d/|\.gitignore$|\.gitattributes$)")


def data_tests(root: Path, files: list[str]) -> tuple[list[str], list[str]]:
    """The tests that name each changed file that isn't Python, and the files
    no test names.

    testmon sees only what coverage sees, which is Python: a changed catalog,
    README or config selects nothing, though tests read them (ba on PR 303:
    one control dropped from ARC-AMPE fails 3 of `test_catalog_readme_counts`'
    8, and testmon selected 0). So such a file adds every test file whose text
    names its path, its file name, or a directory under the top level (a test
    that globs `data/frameworks` names `frameworks`). Selecting too much costs
    time; selecting nothing for a data change is the failure this exists for."""
    corpus = {
        f"tests/{path.name}": path.read_text(encoding="utf-8", errors="replace")
        for path in (root / "tests").glob("test_*.py")
    }
    tests: set[str] = set()
    unmapped: list[str] = []
    for f in files:
        if f.endswith(".py") or _UNTESTED.match(f):
            continue
        parts = f.split("/")
        names = {f, parts[-1], *parts[1:-1]}
        hits = {name for name, text in corpus.items() if any(n in text for n in names)}
        tests |= hits
        if not hits:
            unmapped.append(f)
    return sorted(tests), unmapped


def _importers(root: Path, dotted: str) -> list[str]:
    """`src/` and `scripts/` files that name `dotted`: one more hop, so a test
    reaching the changed module through a script is found (ba on PR 303:
    `test_fresh_corpus.py` reaches `org.context` through
    `scripts/fresh_corpus.py`, which a one-hop grep missed on PR 294)."""
    parent, _, leaf = dotted.rpartition(".")
    pattern = re.compile(
        rf"{re.escape(dotted)}\b|from {re.escape(parent)} import[^\n]*\b{re.escape(leaf)}\b"
    )
    found = []
    for path in [*(root / "src").rglob("*.py"), *(root / "scripts").glob("*.py")]:
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(path.relative_to(root).as_posix())
    return found


def fallback(root: Path, files: list[str]) -> list[str]:
    """The rule's targeted set by hand: changed test files, every test file
    that names a changed module or script (by its dotted path or file stem),
    or names a module or script that names it. (The tests naming changed
    non-Python files are added by `main`, whichever way the rest is chosen.)"""
    tests = {f for f in files if re.fullmatch(r"tests/test_[^/]*\.py", f) and (root / f).exists()}
    modules = set()
    for f in files:
        if f.startswith("src/") and f.endswith(".py"):
            dotted = f[len("src/") : -len(".py")].replace("/", ".").removesuffix(".__init__")
            modules.add(f)
            modules.update(_importers(root, dotted))
        elif f.startswith("scripts/") and f.endswith(".py"):
            modules.add(f)
    names = set()
    for f in modules:
        if f.startswith("src/"):
            dotted = f[len("src/") : -len(".py")].replace("/", ".").removesuffix(".__init__")
            names.add(re.escape(dotted))
            parent, _, leaf = dotted.rpartition(".")
            names.add(rf"from {re.escape(parent)} import[^\n]*\b{re.escape(leaf)}\b")
        else:
            names.add(rf"\b{re.escape(Path(f).stem)}\b")
    if names:
        pattern = re.compile("|".join(names))
        for path in (root / "tests").glob("test_*.py"):
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                tests.add(f"tests/{path.name}")
    return sorted(tests)


def _pytest(*args: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def selected(root: Path) -> list[str]:
    """The test ids testmon would run here, from its data in this worktree."""
    done = _pytest("-q", "--co", "--testmon", "tests/", root=root)
    if done.returncode not in (0, 5):
        raise SystemExit(f"affected_tests: testmon's collection failed:\n{done.stdout[-2000:]}")
    return [line for line in done.stdout.splitlines() if "::" in line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="fj/release/1.8", help="the branch this one targets")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--list", action="store_true", help="say what would run; run nothing")
    parser.add_argument(
        "--fallback", action="store_true", help="ignore testmon data: the targeted set by hand"
    )
    parser.add_argument(
        "--accept-unmapped",
        metavar="REASON",
        help="carry on although a changed non-Python file is named by no test, saying why",
    )
    args = parser.parse_args(argv)
    root = REPO_ROOT
    # coverage's default core on 3.14 is sysmon, which warns that it can't
    # record the per-test contexts testmon attributes by (ba on PR 303).
    os.environ.setdefault("COVERAGE_CORE", "ctrace")

    files = changed(root, args.base)
    python_changed = any(f.endswith(".py") for f in files)
    scan = scanners(root)
    data, unmapped = data_tests(root, files)
    if unmapped and not args.accept_unmapped:
        print("affected_tests: no test names these changed files, so nothing would test them:")
        for f in unmapped:
            print(f"  {f}")
        print("Name the tests that read them and run those, or --accept-unmapped REASON.")
        return 1
    found = None if args.fallback else snapshot_for(root, args.snapshots)
    usable = found is not None and readable(found[0])

    if usable:
        snapshot, commit = found
        shutil.copyfile(snapshot, root / DATA)
        chosen = selected(root)
        source = f"testmon data recorded on {commit[:8]} ({snapshot.name[:12]})"
        if not chosen and python_changed:
            print(f"affected_tests: {source} selects NOTHING, and the branch changes Python.")
            print("An empty selection isn't a pass. Run the targeted set by hand: --fallback")
            return 1
        run = ["--testmon", "tests/"] if chosen else []
        count = f"{len(chosen)} tests in {len({c.split('::')[0] for c in chosen})} files"
    else:
        why = (
            "--fallback given"
            if args.fallback
            else "no snapshot recorded on an ancestor of HEAD"
            if found is None
            else f"{found[0]} is unreadable or empty"
        )
        chosen = fallback(root, files)
        source = f"FALLBACK, {why}: the changed test files and every test naming a changed module"
        run = chosen
        count = f"{len(chosen)} test files"
    print(f"affected: {count}, from {source}")
    print(f"data: {len(data)} test files name the changed non-Python files")
    if unmapped:
        print(f"UNMAPPED, accepted ({args.accept_unmapped}): {', '.join(unmapped)}")
    print(f"scanners: {len(scan)} files, always run")
    if args.list:
        for item in [*chosen, *data]:
            print(f"  {item}")
        return 0

    status = 0
    if run:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rs", "-n", "auto", *run], cwd=root
        )
        status = status or done.returncode
    unselected = sorted(set(scan) | set(data))
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "-n",
            "auto",
            "-p",
            "no:testmon",
            *unselected,
        ],
        cwd=root,
    )
    return status or done.returncode


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the full quality-check suite locally, in one command.

Runs the same checks enforced in .pre-commit-config.yaml and
.github/workflows/ci.yml: ruff (lint + format), pytest, bandit (static
security analysis), semgrep (broader SAST — catches patterns bandit's
Python-specific ruleset doesn't, e.g. GitHub Actions supply-chain hygiene),
pip-audit (dependency CVEs), mdformat (markdown quality), and gitleaks
(secrets scan, if installed).

Ruff's rule selection, line length and per-file ignores live in
pyproject.toml, so this script, the pre-commit hook and CI all enforce
exactly the same thing instead of drifting apart.

Each check is invoked directly rather than through
`pre-commit run` so this has no dependency on pre-commit's hook-environment
builds — notably, pre-commit's official gitleaks hook builds gitleaks from
source via Go on first run, which requires outbound access to Go's module
proxy (proxy.golang.org) and can fail on restrictive corporate networks
even though nothing is actually wrong with your setup. CI doesn't have this
issue: ci.yml downloads the gitleaks binary, pinned by version and sha256,
and runs the same `gitleaks detect` command this script does (#7). This
script's gitleaks check is a local convenience, not the only place it runs.
Here it uses whatever `gitleaks` is on PATH, unpinned; CI's is 8.30.1. Both
scan the history of the commit under test (`--log-opts=HEAD`), not every
ref: see `check_gitleaks`.

Usage:
    python scripts/check.py

Requires: pip install -e ".[dev]"

semgrep is not in the dev extra: it pins its own dependencies exactly, and
installed beside the project those pins became the project's. It is found on
PATH or in `.tools/semgrep`, built from its own hashed lock:

    python -m venv .tools/semgrep
    .tools/semgrep/Scripts/pip install --require-hashes -r requirements/semgrep/semgrep.txt

(`bin/` rather than `Scripts/` outside Windows). Without it, that check is
skipped with a note, as gitleaks is.

Optional: install the gitleaks binary to get the secrets scan locally too
(Windows: `winget install gitleaks.gitleaks`; otherwise download from
https://github.com/gitleaks/gitleaks/releases). Without it, that one check
is skipped with a note, not silently ignored.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import tree_guard

REPO_ROOT = Path(__file__).resolve().parent.parent
#: pytest's report, read by the expected-skips check. Under the gitignored
#: output/, so no run leaves a tracked file behind.
JUNIT = REPO_ROOT / "output" / ".check" / "pytest.xml"

# Checks that can decline to run, and the `--allow-skip` name for each.
# Written out rather than derived from the label, because deriving a key by
# splitting a display string is how you get two checks silently sharing one
# name. A check that returns None without an entry here stops the script.
SKIP_FLAGS = {
    "semgrep (broader SAST)": "semgrep",
    "gitleaks (secrets scan)": "gitleaks",
    "line endings (no CRLF in tracked files)": "git",
    "conflict markers (tracked and untracked)": "git",
}


class EmptyDerivation(RuntimeError):
    """A check derived the set of things it examines, and got nothing.

    **Raised rather than returned, because the alternative is PASS.** Every
    tool this gate drives treats "no input" as success — `mdformat --check`
    with no paths prints *"No files have been passed in. Doing nothing."*
    and exits 0, and `run()` reports `returncode == 0` as a pass. So a
    derivation that stops matching is indistinguishable in the summary from
    a clean tree, and the exit code the charge tells everyone to condition
    their push on is 0.

    **Guarding the class, not the instance.** #224 named the markdown
    population. There are three here — markdown targets from `rglob`,
    tracked files from `ls-files --eol`, and the conflict-marker scan — and
    all three had the same property. A guard written for the one that was
    reported covers the one that was reported; the other two were found by
    asking what else in this file derives a population and then believes a
    clean result over it.

    Deliberately NOT the same mechanism as a SKIP. A skip says a tool was
    absent and is acknowledgeable with `--allow-skip`. This says the tool
    ran and examined nothing, which is never acceptable and must not be
    silenceable by the same flag.
    """


def derived(label: str, items: list, what: str) -> list:
    """Return `items`, or refuse if the derivation produced nothing.

    `what` names the thing that should have been found, in the reader's
    terms, because the failure is always somebody's pathspec and the
    message has to point at it.
    """
    if not items:
        raise EmptyDerivation(
            f"{label}: derived ZERO {what}.\n"
            f"  That is a broken derivation, not a clean tree -- every tool\n"
            f"  here treats no input as success, so this would otherwise\n"
            f"  report PASS having examined nothing."
        )
    return items


def _tool(module: str, *args: str) -> list[str]:
    """A Python tool, run as `<this interpreter> -m <module>`, never by bare name.

    `lock_drift` measures THIS interpreter's libraries (#44). A bare `ruff`
    or `pytest` resolves on PATH, which can be another environment's: with a
    lock-built interpreter and the shared `.venv/Scripts` first on PATH, the
    check read 91/91 same while the shared venv's ruff 0.16.5 ran (9b on
    #46). Only git and gitleaks, which are not Python packages from the
    lock, are run by name.
    """
    return [sys.executable, "-m", module, *args]


def run(label: str, cmd: list[str]) -> bool:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode == 0


#: A line a step wants repeated under its result in the summary, by label.
NOTES: dict[str, str] = {}


def check_semgrep_lock(label: str) -> bool:
    """semgrep's lock, audited in advisory mode (#82; 80's ruling 1774).

    Its output is echoed as it would be, and its `semgrep lock: N advisories
    ...` line is kept for the summary, so a pass that carries advisories is
    never a bare PASS (80's condition 2). A run that printed no such line
    didn't get as far as counting, so it fails whatever its exit code."""
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}", flush=True)
    result = subprocess.run(
        [sys.executable, "scripts/semgrep_lock_audit.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    counted = [line for line in result.stdout.splitlines() if line.startswith("semgrep lock: ")]
    if not counted:
        NOTES[label] = "semgrep lock: NOT COUNTED (the audit didn't finish)"
        return False
    NOTES[label] = counted[-1]
    return result.returncode == 0


#: What gitleaks scans: the history of the commit under test, `HEAD`. The
#: same scope CI's step uses, and a test holds the two equal (#30).
GITLEAKS_ARGS = ("detect", "--verbose", "--redact", "--log-opts=HEAD")


def check_gitleaks() -> bool | None:
    """True/False if it ran, None if skipped (binary not installed).

    **It scans the history of the commit under test, not every ref (#30).**
    Before, it scanned every local ref, and in the shared clone that is every
    session's branches: one session's flagged unpushed commit turned every
    session's gate red (`project_gitleaks_scans_local_refs`). CI had the same
    defect one level up, where the checkout fetches every branch on the
    server, and a secret planted on a throwaway branch turned an unrelated
    PR red. A push gate checks what is being pushed.

    **What it no longer covers:** a secret on some OTHER branch, pushed or
    not. That branch's own gate, and its own CI push run, scan it; it fails
    there instead of here. Measured on a scratch repository (#30): a secret
    only on another branch is not found, and one in HEAD's history is."""
    if shutil.which("gitleaks") is None:
        print(
            f"\n{'=' * 60}\ngitleaks (secrets scan)\n{'=' * 60}\n"
            "SKIPPED — gitleaks binary not found on PATH.\n"
            "Install it once (Windows: `winget install gitleaks.gitleaks`, "
            "or grab a release from "
            "https://github.com/gitleaks/gitleaks/releases), then re-run "
            "this script. CI runs this check regardless (see "
            ".github/workflows/ci.yml) — this is just for a fast local "
            "check before you push."
        )
        return None
    return run(
        "gitleaks (secrets scan)",
        ["gitleaks", *GITLEAKS_ARGS, "--source", str(REPO_ROOT)],
    )


def _tool_roots() -> list[Path]:
    """Where a `.tools/` may be: this checkout, then the MAIN checkout of the
    repository this one is a worktree of (#46). Every session gates in its
    own worktree, whose REPO_ROOT has no `.tools/`; one semgrep built from its
    lock in the shared clone serves them all."""
    roots = [REPO_ROOT]
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir", root=REPO_ROOT)
    if common.returncode == 0 and common.stdout.strip():
        main = Path(common.stdout.strip()).parent
        if main.resolve() != REPO_ROOT.resolve():
            roots.append(main)
    return roots


def find_semgrep() -> str | None:
    """semgrep on PATH, else a `.tools/semgrep` environment (this checkout's,
    then the main checkout's), else None."""
    on_path = shutil.which("semgrep")
    if on_path:
        return on_path
    for root in _tool_roots():
        for scripts in ("Scripts", "bin"):
            found = shutil.which("semgrep", path=str(root / ".tools" / "semgrep" / scripts))
            if found:
                return found
    return None


def check_semgrep() -> bool | None:
    """True/False if it ran, None if skipped (semgrep not installed)."""
    semgrep = find_semgrep()
    if semgrep is None:
        print(
            f"\n{'=' * 60}\nsemgrep (broader SAST)\n{'=' * 60}\n"
            "SKIPPED — semgrep not found on PATH or in .tools/semgrep.\n"
            "It has its own lock, kept out of the dev extra. Install it once:\n"
            "  python -m venv .tools/semgrep\n"
            "  .tools/semgrep/Scripts/pip install --require-hashes "
            "-r requirements/semgrep/semgrep.txt\n"
            "(bin/ instead of Scripts/ outside Windows), then re-run this "
            "script. CI runs this check regardless."
        )
        return None
    # Through semgrep_gate, not `semgrep --error`: --error fails only on
    # findings, so a rule timeout, a partial parse or a file over the size
    # limit was a warning and the step passed (#43).
    return run(
        "semgrep",
        [sys.executable, "scripts/semgrep_gate.py", "--semgrep", semgrep],
    )


def _git(*args: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root or REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _tree_identity(root: Path | None = None) -> str:
    """Which tree a check just examined, not merely how much of it.

    A count answers "was this check empty or did it find nothing" and stops
    there. It does not say *what was in front of it*, and on 2026-09-18 the
    gate printed "321 tracked files scanned, 0 conflict marker(s) found" —
    true of the tree it ran on, false of the tree pushed a minute later,
    because the offending file was still untracked. Every check that reads
    the repository prints this line so the answer is attached to a tree
    rather than floating free.
    """
    head = _git("rev-parse", "--short", "HEAD", root=root)
    sha = head.stdout.strip() if head.returncode == 0 else "no commits"
    # `??` is an untracked file and is counted separately below. Reading
    # its second column as a modification state counts it as "unstaged",
    # which is how the first version of this line reported one untracked
    # file as "0 staged, 1 unstaged, 1 untracked" — the same file twice,
    # once under a name that does not describe it.
    dirty = [
        line
        for line in _git("status", "--porcelain", root=root).stdout.splitlines()
        if line.strip() and not line.startswith("??")
    ]
    staged = sum(1 for line in dirty if line[:1] not in {" ", ""})
    unstaged = sum(1 for line in dirty if line[1:2] not in {" ", ""})
    others = len(
        [
            line
            for line in _git(
                "ls-files", "--others", "--exclude-standard", root=root
            ).stdout.splitlines()
            if line.strip()
        ]
    )
    state = "clean" if not dirty else f"{staged} staged, {unstaged} unstaged"
    return f"tree: HEAD {sha}, {state}, {others} untracked (not ignored)"


def check_line_endings(root: Path | None = None) -> bool | None:
    """No tracked file may carry CRLF in the index. None if git is absent.

    `.gitattributes` pins `*.md text eol=lf`, so markdown is safe whatever
    `core.autocrlf` says — verified by writing CRLF markdown into a repo
    with autocrlf off and watching the blob come back LF. Nothing protects
    `.yaml`, `.json`, `.toml` or `.py`, and on a Windows machine with
    autocrlf off those commit exactly as written.

    Asked of git rather than by reading bytes: `ls-files --eol` reports the
    index blob, which is what ships, and a working tree legitimately holds
    CRLF under autocrlf=true. Reading the working tree would flag every
    Windows checkout; reading the blob flags only what was committed.

    Untracked files are deliberately NOT examined here, unlike the
    conflict-marker check. A CRLF working-tree file is not yet a defect:
    `core.autocrlf` and `.gitattributes` normalise at `git add`, so the
    blob it becomes may well be LF, and flagging it would fire on every
    Windows checkout. The count of untracked files is printed instead, so
    the gap is stated rather than silent.
    """
    label = "line endings (no CRLF in tracked files)"
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    print(_tree_identity(root))
    result = _git("ls-files", "--eol", root=root)
    if result.returncode != 0:
        print(f"SKIPPED — `git ls-files --eol` failed:\n{result.stderr.strip()}")
        return None

    rows = derived(
        label,
        [line for line in result.stdout.splitlines() if line.strip()],
        "tracked files -- `git ls-files --eol` returned nothing",
    )
    offenders = [line for line in rows if line.split()[0] in {"i/crlf", "i/mixed"}]
    print(f"{len(rows)} tracked files examined, {len(offenders)} carrying CRLF in the index")
    for line in offenders:
        print(f"  {line}")
    if offenders:
        print(
            "\nA file written with `Path.write_text` on Windows gets CRLF unless\n"
            '`newline="\\n"` is passed. Renormalise with `git add --renormalize <file>`.'
        )
    return not offenders


def check_conflict_markers(root: Path | None = None) -> bool | None:
    """No file about to be committed may contain a merge-conflict marker.

    `git add -A` mid-rebase happily stages a file git still reports as
    unresolved, and the rebase then completes clean. Every catch we have is
    incidental: a marker is a Python syntax error, and `=======` reads as a
    setext heading underline to mdformat. Nothing in the gate parses YAML
    at all, and `.yaml` covers every workflow file and `framework.yaml`.

    Scans UNTRACKED files too, via `git grep --untracked`, which still
    honours `.gitignore`. An earlier version read tracked files only and so
    could not see the incident that produced it: the file carrying the
    markers was untracked when the gate ran and committed a minute later,
    and the gate reported "0 conflict marker(s) found" over a tree that did
    not contain it. The untracked file is precisely the one about to become
    a commit, so it is the one that most needs looking at.

    Deliberately does NOT search for `=======`. Seven equals signs are a
    legal setext heading underline and appear in ordinary prose, and git
    never writes a `=======` without the `<<<<<<< ` that opens the
    conflict — so the opening and closing markers are both sufficient and
    free of false positives, which is what lets this run tree-wide with no
    suppression list. (A tree-wide grep for `=======` was in fact tried
    while writing this and reported two hits in README.md: 60-character
    rules inside a fenced sample-output block.)
    """
    label = "conflict markers (tracked and untracked)"
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    print(_tree_identity(root))
    unambiguous = r"^(<<<<<<< |>>>>>>> |\|\|\|\|\|\|\| )"
    result = _git("grep", "-I", "-n", "-E", "--untracked", unambiguous, "--", ".", root=root)
    if result.returncode not in (0, 1):
        print(f"SKIPPED — `git grep` failed:\n{result.stderr.strip()}")
        return None

    hits = [line for line in result.stdout.splitlines() if line.strip()]

    # The corpus is built as a list and guarded, rather than counted twice:
    # `tracked` and `others` are the numbers the message prints, and the
    # thing that must not be empty is what was actually scanned.
    tracked_files = [
        line for line in _git("ls-files", root=root).stdout.splitlines() if line.strip()
    ]
    other_files = [
        line
        for line in _git(
            "ls-files", "--others", "--exclude-standard", root=root
        ).stdout.splitlines()
        if line.strip()
    ]
    # Guarded as one corpus: what must not be empty is what was scanned.
    # `tracked` and `others` below are measurements OF this list, so they
    # cannot disagree with it -- the earlier version called `ls-files`
    # twice and counted one of them separately.
    # Called for the refusal, not for a value: `tracked` and `others`
    # below are measurements of the same two lists.
    derived(
        label,
        tracked_files + other_files,
        "files to scan -- `git ls-files` returned no corpus",
    )
    # `tracked` and `others` are measurements OF `corpus`, so they cannot
    # disagree with it. An earlier version asserted that they summed to
    # `len(corpus)`; policyforge-ba pointed out that restates the
    # construction and no input can make it differ -- **a line that reads
    # as a check and is not one**, in the file that now exists to catch
    # exactly that. It was also a bare `assert`, which `python -O` strips.
    tracked, others = len(tracked_files), len(other_files)
    print(
        f"{tracked} tracked + {others} untracked files scanned, "
        f"{len(hits)} conflict marker(s) found"
    )
    for line in hits:
        print(f"  {line}")
    if hits:
        print(
            "\nResolve the file and `git add` it by name. `git add -A` during a\n"
            "rebase stages conflicts as though they were resolved."
        )
    return not hits


def parse_allow_skip(argv: list[str] | None) -> set[str]:
    """Tool names the caller has accepted as missing.

    `argv or []`, never argparse's default of falling back to sys.argv:
    `main()` is called directly by tests/test_tree_guard.py, and inside a
    pytest process sys.argv holds pytest's flags. Left to the default,
    `main()` died on pytest's own `-q` — caught by those tests, which call
    it for an unrelated reason, and by none of the ones written alongside
    this change.
    """
    parser = argparse.ArgumentParser(
        prog="check.py",
        description="Run the full quality-check suite locally, in one command.",
    )
    parser.add_argument(
        "--allow-skip",
        action="append",
        default=[],
        choices=sorted(set(SKIP_FLAGS.values())),
        metavar="TOOL",
        help=(
            "Accept that this tool is missing and let the gate still pass. "
            "Repeatable. Without it, a check that did not run fails the gate."
        ),
    )
    return set(parser.parse_args(argv or []).allow_skip)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except EmptyDerivation as exc:
        # Caught here so the reader gets a gate result rather than a
        # traceback, and so the exit code is a deliberate 2 -- distinct
        # from 1 (a check failed) because nothing was actually checked.
        rule = "=" * 60
        print(f"\n{rule}\nBROKEN DERIVATION\n{rule}\n{exc}", file=sys.stderr)
        return 2


def _main(argv: list[str] | None = None) -> int:
    allow_skip = parse_allow_skip(argv)

    # Before anything runs: the `policyforge` every check below would import
    # has to be this tree's. In a git worktree it is not — the editable
    # install pins the checkout it was made from — and the whole gate would
    # report on code it never loaded. Refused, with the fix, rather than
    # passed quietly (see scripts/tree_guard.py).
    refusal = tree_guard.foreign_source(
        tree_guard.resolved_origin(), REPO_ROOT, invocation="python scripts/check.py"
    )
    if refusal:
        print(refusal, file=sys.stderr)
        return 2

    # Every markdown file the pre-commit mdformat hook would touch, so this
    # script and that hook can't disagree about what "formatted" means. Only
    # output/ is excluded — it holds generated drafts, which are checked by
    # `check_markdown_quality` at generation time instead.
    md_targets = derived(
        "mdformat (markdown quality)",
        sorted(
            str(p)
            for p in REPO_ROOT.rglob("*.md")
            if not any(
                part in {".venv", ".tools", "output", "local_content", ".git", ".pytest_cache"}
                for part in p.relative_to(REPO_ROOT).parts
            )
        ),
        "markdown files -- the rglob or the exclusion set stopped matching",
    )

    lint_targets = ["src", "tests", "scripts"]

    semgrep = find_semgrep()
    results: dict[str, bool | None] = {
        # First, so every result below says which libraries it measured:
        # CI installs the locks, and a drifted environment makes this gate
        # answer a different question from CI's (#44; #31's 87 failures).
        "environment matches CI's locks": run(
            "lock_drift",
            [sys.executable, "scripts/lock_drift.py", *(["--semgrep", semgrep] if semgrep else [])],
        ),
        # Lint/format first: they're the fastest checks and the most likely
        # to fail on a fresh edit, so failing here saves waiting on semgrep.
        "ruff (lint)": run("ruff check", _tool("ruff", "check", *lint_targets)),
        "ruff (format)": run(
            "ruff format --check", _tool("ruff", "format", "--check", *lint_targets)
        ),
        "pytest (test suite)": run("pytest", _tool("pytest", "-q", "-rs", f"--junitxml={JUNIT}")),
        # The same list CI checks (#31). The project venv has every extra
        # installed and skips exactly the listed tests, as CI now does, so
        # one list holds on both. A machine missing an extra or the gitleaks
        # binary fails here, naming the skipped test: install it, rather
        # than let the gate cover less than CI.
        "expected skips only": run(
            "skip_gate", [sys.executable, "scripts/skip_gate.py", str(JUNIT)]
        ),
        # Build the public commit P as the cut would and run the publish guard
        # on it, every gate run (#49). Until this existed the first real P the
        # guard saw was going to be the release, and a docstring in
        # release.py was refusing it, unseen, for a day.
        "public commit passes the publish guard": run(
            "public_rehearsal", [sys.executable, "scripts/public_rehearsal.py", "--worktree"]
        ),
        "bandit (static security analysis)": run(
            "bandit", _tool("bandit", "-c", "pyproject.toml", "-r", "src")
        ),
        "semgrep (broader SAST)": check_semgrep(),
        "pip-audit (dependency CVEs)": run("pip-audit", _tool("pip_audit")),
        # Advisory mode for semgrep's own lock only (#82; 80's ruling 1774):
        # every advisory is listed and counted, and the count is repeated in
        # the summary. The gate, CI and ci_in_docker all run the same script.
        "pip-audit (semgrep's lock)": check_semgrep_lock("pip-audit (semgrep's lock)"),
        "mdformat (markdown quality)": run(
            "mdformat --check", _tool("mdformat", "--check", *md_targets)
        ),
        # **CI requires this and the gate did not run it.** Observed live on
        # #211: this script reported 2,921 tests passing and every check
        # green while CI was red on `changelog`. A pre-push gate that a
        # required check can fail behind is not a gate -- it is a subset
        # someone has to remember is a subset.
        #
        # Not skippable. The other four skips exist because a tool may be
        # absent; this one is a script in this repository, so "it did not
        # run" has no honest cause.
        "changelog fragments (shape)": run(
            "changelog_fragments --check",
            [sys.executable, "scripts/changelog_fragments.py", "--check"],
        ),
        "committed shell (exit status)": run(
            "shell_status",
            [sys.executable, "scripts/shell_status.py"],
        ),
        "gitleaks (secrets scan)": check_gitleaks(),
        # Tree hygiene last: both are fast, and both catch a class the rest
        # of the gate only ever caught by accident.
        "line endings (no CRLF in tracked files)": check_line_endings(),
        "conflict markers (tracked and untracked)": check_conflict_markers(),
    }

    return summarise(results, allow_skip)


def summarise(results: dict[str, bool | None], allow_skip: set[str]) -> int:
    """Print the summary and return the exit code.

    Separate from `main` so the exit rule can be tested without installing
    or uninstalling tools. The rule it encodes: **a check that did not run
    is not a check that passed.**

    **And a gate with no checks is not a passing gate.** `summarise({}, set())`
    returned 0 and printed `0 ran, 0 failed, 0 skipped` -- the same failure
    this file's `derived()` guard exists to refuse, one level up, in the
    function that produces the answer everyone conditions their push on.

    policyforge-ba found it and argued it was categorically different,
    because `results` is a dict literal whose keys cannot shrink without a
    visible diff. That is true of the code as it stands today and it is an
    argument from the current shape rather than from a guard -- it stops
    holding the moment anyone builds `results` conditionally, which is a
    two-line change nobody would flag. So it is refused here instead.
    """
    if not results:
        print(
            "check.py: ZERO checks ran.\n"
            "  That is not a clean tree; it is a gate that did not assemble.",
            file=sys.stderr,
        )
        return 2
    # A check that declined to run but has no --allow-skip name would
    # fall out of the accounting below and be reported as a pass. Stop
    # instead: the failure mode this whole change exists to remove is
    # exactly "absent, and therefore counted as fine".
    undeclared = [
        label for label, passed in results.items() if passed is None and label not in SKIP_FLAGS
    ]
    if undeclared:
        print(
            f"\nBUG in check.py: check(s) skipped with no SKIP_FLAGS entry: {undeclared}",
            file=sys.stderr,
        )
        return 2

    skipped = [label for label, passed in results.items() if passed is None]
    unacknowledged = [label for label in skipped if SKIP_FLAGS[label] not in allow_skip]

    # What did NOT run comes first, before the wall of PASS. A reader
    # scanning a summary is looking for the word FAIL, and a SKIP sitting
    # seventh in a list of nine reads as noise next to eight passes.
    print(f"\n{'=' * 60}\nSummary\n{'=' * 60}")
    if skipped:
        print(f"  {len(skipped)} of {len(results)} checks DID NOT RUN:")
        for label in skipped:
            note = "" if SKIP_FLAGS[label] in allow_skip else "  <- not acknowledged"
            print(f"      {label}{note}")
        print()

    for label, passed in results.items():
        status = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
        print(f"  {status}  {label}")
        if label in NOTES:
            print(f"        {NOTES[label]}")

    failed = [label for label, passed in results.items() if passed is False]
    print(f"\n  {len(results) - len(skipped)} ran, {len(failed)} failed, {len(skipped)} skipped")

    if unacknowledged:
        # The whole point of this script is to answer "is this safe to
        # push". A tool that is absent has produced no evidence, and
        # reporting its absence as success is the one answer that cannot
        # be recovered from downstream: the reader has already stopped
        # looking. Acknowledging a skip is cheap and leaves a record of
        # who decided the gap was acceptable.
        flags = " ".join(f"--allow-skip {SKIP_FLAGS[label]}" for label in unacknowledged)
        print(
            f"\n  FAILING because {len(unacknowledged)} check(s) did not run.\n"
            f"  Install the missing tool (each SKIP above says how), or accept\n"
            f"  the gap explicitly:\n\n      python scripts/check.py {flags}\n"
        )
        return 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

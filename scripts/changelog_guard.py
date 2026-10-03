#!/usr/bin/env python3
"""Fail a pull request that changes what a user sees and says nothing about it.

Two merges on 2026-09-18 shipped with no `CHANGELOG.md` entry. One added a
config flag that runs a **second model** on the answering path — a new cost,
a new external call and a new failure mode — and a user upgrading had no way
to learn it existed. Both pull requests were reviewed properly. Every
per-change check passed. The gap is invisible from any diff, because it is
not a property of the change: it is the answer to *does the changelog
describe what main now does*, and nothing was asking that.

A rule for this already existed, written down after an earlier instance, and
was not run. That is the third time in one evening that someone failed to
apply a rule they personally wrote, hours earlier, about the situation they
were in. The common factor is not carelessness: all three are rules you must
**remember to apply at the moment you are finishing something**, which is
when attention is lowest and the rule is due. `.github/PULL_REQUEST_TEMPLATE.md`
already carries a changelog line in its checklist, and a checklist is the
remembered form. This is the same rule in the form that fires by itself.

WHAT COUNTS AS USER-FACING is deliberately narrow and listed rather than
inferred. A check that trips on refactors gets routed around, and a check
people route around is worse than none — it trains the habit of skipping.
The list is the surfaces where a change alters what someone running the tool
experiences: commands, the documented config, the prompts that shape
generated output, and the bundled catalogs.

THE OPT-OUT IS THE POINT, not a concession. Some changes legitimately touch
these paths and need no entry — a line reflow in a prompt where a word-level
diff against main is empty is a real example from the same evening, and a
check that forced an entry for it would be teaching people to write noise.
Declaring it in the pull request body turns an omission into a decision with
a reason attached, which is the whole difference this script exists to make.

Usage (CI passes the two facts it cannot know):

    python scripts/changelog_guard.py --base origin/main --body-file body.txt

Exit 0 when an entry is present, when nothing user-facing changed, or when
the body carries a declared exemption. Exit 1 otherwise, naming the paths.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CHANGELOG = "CHANGELOG.md"

#: Fragments live here, one file per branch, assembled at release by
#: `scripts/changelog_fragments.py`. A new file cannot conflict with
#: another branch's new file, which is the whole point.
FRAGMENT_DIR = "changelog.d/"

#: Surfaces where a change alters what someone running the tool experiences.
#: Listed rather than inferred, and kept narrow on purpose — see the module
#: docstring on why a check that trips on refactors is worse than no check.
USER_FACING = (
    # Commands, their arguments, their output and their exit codes.
    "src/policyforge/cli/",
    # The documented configuration surface. A new key here is a new thing a
    # user can switch on; this is what would have caught `entail.answering`.
    "config/config.example.yaml",
    # Prompts that shape generated documents. What these say is what the
    # user reads in the output.
    "src/policyforge/synthesis/",
    "src/policyforge/generate/",
    "src/policyforge/edit/",
    # Bundled catalogs: a new one, or a changed one, is shipped content.
    "data/frameworks/",
    # Entry points, dependencies and the supported Python range.
    "pyproject.toml",
)

#: How a pull request declares that no entry is needed. A reason is required:
#: a bare marker would be a silent exemption wearing a word, and the record
#: is the reason rather than the marker.
EXEMPTION = re.compile(r"^\s*no changelog entry:\s*(?P<reason>\S.*)$", re.IGNORECASE | re.MULTILINE)


def user_facing(paths: list[str]) -> list[str]:
    """The subset of `paths` that changes what a user experiences."""
    return sorted(p for p in paths if any(p.startswith(prefix) for prefix in USER_FACING))


def decide(paths: list[str], body: str) -> tuple[bool, str]:
    """(ok, message) for a pull request touching `paths` with `body`.

    Separate from the git and CI plumbing so the rule can be tested against
    real pull requests from history rather than against a mock.
    """
    # A fragment satisfies this as fully as an edit to CHANGELOG.md, and is
    # the preferred form — `changelog.d/` exists so branches stop conflicting
    # in one shared section. Without this clause the guard would push every
    # author back into the file the fragments were built to keep them out of,
    # which is how a well-meant check defeats the change it sits beside.
    # The README exclusion belongs INSIDE the filter, not after it. Written
    # as `next(...)` then `if not ...README.md`, this took the first `.md`
    # under changelog.d/ and only then asked whether it was the README — so a
    # branch adding a real fragment AND touching the README was rejected,
    # because `git diff --name-only` sorts and uppercase `R` sorts first.
    # The boolean was a true answer about the first file it happened to see
    # rather than about the branch, and the case it broke is the likely one:
    # whoever writes the first fragment and improves the instructions while
    # they are in there. Found by 9b reviewing this clause.
    fragment = next(
        (
            p
            for p in paths
            if p.startswith(FRAGMENT_DIR) and p.endswith(".md") and not p.endswith("/README.md")
        ),
        None,
    )
    if fragment:
        return True, f"{fragment} is in this change."

    if CHANGELOG in paths:
        return True, f"{CHANGELOG} is in this change."

    touched = user_facing(paths)
    if not touched:
        return True, f"No user-facing path changed ({len(paths)} file(s) examined)."

    exemption = EXEMPTION.search(body or "")
    if exemption:
        return True, f"Exempted in the pull request body: {exemption.group('reason').strip()}"

    listed = "\n".join(f"    {p}" for p in touched)
    return False, (
        f"{len(touched)} user-facing path(s) changed with no changelog entry:\n"
        f"{listed}\n\n"
        f"Add a file in {FRAGMENT_DIR} named after your branch, saying what a\n"
        "user does differently — not what the code now does. Editing\n"
        f"{CHANGELOG} directly also satisfies this, but fragments do not\n"
        "conflict with other branches and that is why they exist.\n\n"
        "If no entry is genuinely needed, say so in the pull request\n"
        "body on its own line, with the reason:\n\n"
        "    No changelog entry: <why>\n\n"
        "e.g. 'No changelog entry: pure reflow, word-level diff against main\n"
        "is empty'. The reason is the record; that is the point."
    )


def changed_paths(base: str, root: Path | None = None) -> list[str]:
    """Files this branch changes relative to `base`, via the merge-base.

    `git diff base...HEAD` — three dots — so a branch that is merely behind
    does not inherit everything main has done since. Diffing against the tip
    of main instead is how a rebase artefact reads as a real change, which
    misfired four separate times in one evening on this repository.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"changelog_guard: `git diff {base}...HEAD` failed:\n{result.stderr}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def base_hint(base: str) -> str:
    """The command the caller is effectively running, for the dirty-tree note."""
    return f"git diff --name-only {base}...HEAD"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main", help="branch point to diff against")
    parser.add_argument("--body-file", type=Path, help="file holding the pull request body")
    args = parser.parse_args(argv or [])

    body = ""
    if args.body_file and args.body_file.exists():
        body = args.body_file.read_text(encoding="utf-8", errors="replace")

    paths = changed_paths(args.base)

    # `base...HEAD` compares COMMITS. Run locally on work that is only staged
    # or only edited, it examines nothing and passes — which is a vacuous
    # pass wearing a green tick, and it happened on the first run of this
    # script against its own branch. In CI the head is always committed, so
    # this cannot fire there; locally it is the difference between "nothing
    # user-facing changed" and "I could not see your change".
    if not paths:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout.strip()
        if dirty:
            print(
                f"changelog guard: examined 0 files against {args.base}, but the working\n"
                f"tree has uncommitted changes. `{base_hint(args.base)}` compares commits;\n"
                "commit first, or this says nothing about the work you are looking at.",
                file=sys.stderr,
            )
            return 2
    ok, message = decide(paths, body)
    print(f"changelog guard: {'PASS' if ok else 'FAIL'}\n{message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

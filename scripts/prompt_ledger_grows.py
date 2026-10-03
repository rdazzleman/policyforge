#!/usr/bin/env python3
"""The prompt version ledger only grows: nothing the base branch recorded is
changed or removed (#424).

    python scripts/prompt_ledger_grows.py --base origin/release/1.7

`evals/prompt-versions.json` records every version each prompt has declared
and the one text it had, as a fingerprint (#236). `tests/test_prompt_fingerprint_file.py`
holds it equal to the registry, but it compares the code with the ledger AS
COMMITTED IN THE SAME CHANGE, so an edit that changes a prompt's text at its
version and rewrites that version's recorded fingerprint passes it. That is
#117's shape with the guard's expected side edited to agree (1d on #422). This
compares the ledger with the BASE branch's, which the change cannot edit.

**Exit codes, and why there are three.** 0 the ledger only grew; 1 something
recorded on the base was changed or removed, each named; 2 **no answer**: the
base does not resolve, so nothing was compared. A check that passes on a base
it could not read is worse than the review it replaces (policyforge-b5 on
#422), so 2 is a failure, never a pass. A base that resolves and holds NO
ledger (a branch from before #236) is a readable fact, not a missing answer:
there is nothing recorded to hold, and it says so rather than passing quietly.

**Against the branch point, not the base's tip** (policyforge-ba on #435).
The ledger is compared with `git merge-base HEAD <base>`. Run locally on
a branch whose base has since grown, the tip holds versions this branch
never saw, and comparing with it reported them "removed here": a false
alarm, and a check that cries wolf gets ignored. In CI the checkout is
the pull request's merge commit, whose merge-base with the base IS the
tip, so CI's answer does not change.

**What this does not hold** (policyforge-9b on #435): an ADDITION passes by
design, including an invented one, a version that never existed recorded
as though it had. Nothing here can tell a real new version from a made-up
historical one; additions are held by review of the ledger's diff.

CI checks out the pull request with full history (`fetch-depth: 0`), so
`origin/<base>` exists; run locally, fetch first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = "evals/prompt-versions.json"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8"
    )


def compare(base: dict, head: dict) -> list[str]:
    """Every entry recorded on the base that is not identical at the head.

    A version's entry list is compared whole (fingerprint and `first_seen`),
    so rewriting either is caught. A prompt that left the registry keeps its
    history here, so a removed prompt is a removed record, too.
    """
    problems = []
    for name, versions in sorted(base.items()):
        for version, entries in sorted(versions.items(), key=lambda kv: int(kv[0])):
            now = head.get(name, {}).get(version)
            if now is None:
                problems.append(f"{name} v{version}: recorded on the base, removed here")
            elif now != entries:
                was = ", ".join(e.get("fingerprint", "?") for e in entries)
                is_ = ", ".join(e.get("fingerprint", "?") for e in now)
                problems.append(
                    f"{name} v{version}: recorded as [{was}] on the base, [{is_}] here "
                    "(a version keeps its text for life: give the new text a new version)"
                )
    return problems


def main(argv: list[str] | None = None, root: Path = ROOT, out=print) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="the base ref, e.g. origin/release/1.7")
    parser.add_argument("--path", default=LEDGER)
    args = parser.parse_args(argv)

    if _git(root, "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}").returncode != 0:
        out(f"NO ANSWER: base {args.base!r} does not resolve here, so nothing was compared.")
        out("  In CI the checkout needs fetch-depth: 0; locally, fetch the base first.")
        return 2

    fork = _git(root, "merge-base", "HEAD", args.base)
    point = (fork.stdout or "").strip()
    if fork.returncode != 0 or not point:
        out(f"NO ANSWER: HEAD and {args.base!r} share no history, so nothing was compared.")
        return 2
    out(f"comparing with {args.base} at the branch point {point[:12]}")

    shown = _git(root, "show", f"{point}:{args.path}")
    if shown.returncode != 0:
        if _git(root, "cat-file", "-e", f"{point}:{args.path}").returncode != 0:
            out(f"{args.base} holds no {args.path}: nothing recorded there to hold.")
            return 0
        out(f"NO ANSWER: {args.base}:{args.path} exists and could not be read.")
        return 2
    try:
        base = json.loads(shown.stdout)["prompts"]
    except (json.JSONDecodeError, KeyError) as exc:
        out(f"NO ANSWER: {args.base}:{args.path} is not a ledger ({exc!r}).")
        return 2

    here = root / args.path
    if not here.exists():
        out(f"{args.path} is gone here, and {args.base} recorded {len(base)} prompt(s) in it.")
        return 1
    head = json.loads(here.read_text(encoding="utf-8"))["prompts"]

    recorded = sum(len(v) for v in base.values())
    now = sum(len(v) for v in head.values())
    problems = compare(base, head)
    out(
        f"prompt ledger vs {args.base}: {recorded} version(s) recorded there, "
        f"{now} here, {len(problems)} changed or removed"
    )
    for line in problems:
        out(f"  {line}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

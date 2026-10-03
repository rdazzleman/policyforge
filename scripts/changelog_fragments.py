#!/usr/bin/env python3
"""Assemble `changelog.d/` fragments into a release section of CHANGELOG.md.

WHY THIS EXISTS. One `## Unreleased` section means every pair of branches
shipping anything user-visible edits the same place in the same file, so
every pair conflicts. In a single sitting on 2026-09-18 that produced:

  - four hand-resolved conflicts in `Unreleased`
  - one resolution that silently converted 1,768 lines to CRLF, found by
    diffing a normalised copy rather than by running the formatter, which
    would have rewritten every line and hidden the one real change
  - one resolution that left an entry as a `###` heading, so five unrelated
    entries nested under "The HIPAA catalog verifies" — no conflict, no
    failing check, every merge individually correct, and only visible in the
    assembled text after the fifth merge
  - two user-visible merges that shipped with no entry at all

Every one of those is a property of *where the text lives*, not of anyone's
care. `scripts/changelog_guard.py` (#126) makes the last of them fail CI.
This removes the cause of the other three: a fragment is a new file, and new
files do not conflict.

WHAT THIS DOES NOT DO, deliberately. It does not order the entries, group
them into categories, or invent headings. This changelog's voice is bold-lead
paragraphs in a deliberate narrative order — breaking changes first, the
reason before the remedy — and a tool that imposed `### Added` / `### Fixed`
would flatten that into something nobody here would have written. Assembly
emits fragments in filename order and the person cutting the release arranges
them, **once, in one file, with no conflict to resolve**. The value is
removing the conflict, not automating the judgement.

Usage:

    python scripts/changelog_fragments.py --check          # CI: are they well-formed
    python scripts/changelog_fragments.py --version 1.5.0  # cut the section
    python scripts/changelog_fragments.py --version 1.5.0 --dry-run

Writing one: a file in `changelog.d/` named after your branch, holding the
prose you would otherwise have put under `## Unreleased`. See
`changelog.d/README.md`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = REPO_ROOT / "changelog.d"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

#: `README.md` documents the convention and is never an entry.
NOT_A_FRAGMENT = {"README.md"}

#: Where a new section is inserted: immediately before the newest existing
#: release heading, so the file stays newest-first without needing to know
#: what the previous version was.
RELEASE_HEADING = re.compile(r"^## \d+\.\d+\.\d+", re.MULTILINE)


def fragments(directory: Path | None = None) -> list[Path]:
    """Fragment files in filename order, excluding the README."""
    directory = directory or FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if p.name not in NOT_A_FRAGMENT)


#: An ATX heading at a level that cannot appear inside a release section.
#: **By level, not by literal text.** The old rule was `startswith("## ")` —
#: the level that was observed on #211, and not the level that was not.
#: `# ` sits even higher and was accepted. `###` and deeper are legitimate
#: sub-structure inside a fragment and must keep passing.
_TOO_HIGH = re.compile(r"^(#{1,2})(?:\s|$)")

#: A fenced block opens or closes here. What is inside a fence is an
#: example, not structure: a fragment showing what an assembled changelog
#: looks like is a legitimate thing to write, and the old line-oriented
#: scan refused it.
_FENCE = re.compile(r"^\s*(?:```|~~~)")


_WHERE = r"(?:above|below)"

#: A pointer to another entry by where it sits (#387). Assembly orders
#: fragments by filename, author prefix first, so "see below" is a guess
#: about an order its writer cannot see from their own file: three were
#: written in one hour of 1.6.1's fixes, one per author, and every one
#: pointed the wrong way or was right by luck.
#:
#: **A pointer, not every "above" or "below".** Measured against the 3,691
#: lines of `CHANGELOG.md` at 1.6.1: the bare words matched 19 times. Ten
#: were pointers to other entries and nine were prose that must keep
#: passing ("would fall below the catalog", "mcp is held below 2", "the
#: requirement above it", "the note above the rows");
#: `tests/test_changelog_fragments.py` holds all nine, so a later widening
#: that reintroduces one fails.
#:
#: **Of the ten pointers, eight are matched here, one is skipped on
#: purpose, and one is missed** (1d and policyforge-b5 on #420).
#:
#: - Matched: "see below" (with an adverb: "see further below"), a noun
#:   naming an entry beside a position ("the entry above", "the harness
#:   change below", "the ledger fix above"), a quoted entry title followed
#:   by one, "the above" standing alone, and "everything else below".
#: - Skipped: `<- see below` inside a fenced table (1.6.0). A fence is an
#:   example, not prose, by the same rule as the heading check.
#: - **Missed: a pointer whose subject is any other noun**: "the Part 2
#:   catalog above" (1.5.0), "the ledger bug above". That vocabulary is
#:   open, and a noun list would never finish, so for that shape review is
#:   still the instrument. The test file holds the real one as a strict
#:   expected miss, red the day this starts to catch it.
#:
#: **Also unmatched, deliberately: time words.** "see earlier in these
#: notes", "the preceding fix", "the following change", "a later fix".
#: Only "previous/next/preceding/following entry" is refused. In
#: `CHANGELOG.md` the family occurs twice and both are prose ("a later fix
#: would need", "the first fix still let"), so refusing it would cry wolf
#: at the measured rate.
#:
#: **A comparison is not a pointer.** A noun then a position then a
#: complement ("a change below 5%", "a fix below 1.0", "below the
#: catalog") compares; a pointer usually has nothing after the position.
#: **Usually, and that is the price** (policyforge-b5 on #420): a pointer
#: followed by `it`, `the`, `a` or a number reads as a comparison and
#: passes ("The fix above it also covers HIPAA."). The test file holds it
#: as a strict expected miss. Refusing it would refuse "a change below
#: 5%" with it, and comparisons are the commoner shape in this project's
#: notes. "You see
#: below" describes rather than directs, and passes. `section` is not in the
#: noun list: a fragment may use `###`, so "the section below" is within it.
_ADVERB = r"(?:(?:the|further|just|also|directly|immediately)\s+)?"
_COMPLEMENT = r"(?!\s+(?:\d|the\b|a\b|an\b|it\b))"
_POSITIONAL = re.compile(
    "|".join(
        [
            rf"(?<!\byou )(?<!\bwe )\bsee\s+{_ADVERB}{_WHERE}\b",
            rf"\b(?:entry|entries|fragment|fragments|change|fix|item)\s+{_WHERE}\b{_COMPLEMENT}",
            rf"\b{_WHERE}\s+(?:entry|entries|fragment|fragments)\b",
            r"\b(?:previous|next|preceding|following)\s+(?:entry|entries|fragment|fragments)\b",
            rf"\"[^\"]{{3,}}\"\s+{_WHERE}\b",
            # "the above" as a noun: followed by punctuation or the end.
            rf"\bthe\s+{_WHERE}(?=\s*(?:[,.;:)]|$))",
            rf"\b(?:everything|all|the\s+rest)(?:\s+else)?\s+{_WHERE}\b{_COMPLEMENT}",
        ]
    ),
    re.IGNORECASE,
)

#: Inline code, which quotes rather than says.
_CODE_SPAN = re.compile(r"`[^`]*`")


def positional_pointers(text: str) -> list[str]:
    """Each pointer to another entry by position, outside code (#387)."""
    prose: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            prose.append(_CODE_SPAN.sub(" ", line))
    # Joined, because a pointer wraps across a line: "the entry" then "above".
    joined = " ".join(prose)
    return [" ".join(m.group(0).split()) for m in _POSITIONAL.finditer(joined)]


def strays(directory: Path | None = None) -> list[Path]:
    """Files sitting in `changelog.d/` that `fragments()` will never return.

    **The silent-drop half of #217.** `changelog.d/my-fix.txt` is written,
    committed, passes the gate green and passes CI green — and the entry
    never reaches the changelog, because `fragments()` globs `*.md` and
    nothing anywhere says a file was ignored.
    """
    directory = directory or FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix != ".md" and p.name not in NOT_A_FRAGMENT
    )


def check(directory: Path | None = None) -> list[str]:
    """Complaints about anything in `changelog.d/` that would not assemble.

    Deliberately few, and each is a thing that produced a real defect: an
    empty fragment means someone opened the file and did not write the
    entry; a heading above `###` creates a second release section inside
    the one being cut; a CR is the 1,768-line conversion in miniature; a
    non-`.md` file is an entry nobody will ever read; and "see below"
    points the wrong way as often as not (#387).

    **It takes the DIRECTORY rather than a list of paths, and that is a fix
    rather than a matter of taste.** The old signature was `check(paths)`,
    and every caller and every test spelled it `check(fragments(...))` — so
    a stray file was filtered out one call before the check could see it,
    and no test written that way could have caught it. **A check handed its
    population cannot guard that population.** This one derives its own.
    """
    problems = []
    for path in strays(directory):
        problems.append(
            f"{path.name}: is in {FRAGMENT_DIR.name}/ and is not a `.md` fragment, so it "
            "will never be assembled. Rename it to `.md` or move it out."
        )
    for path in fragments(directory):
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            problems.append(f"{path.name}: empty — write the entry or delete the file")
        if b"\r" in raw:
            problems.append(f"{path.name}: contains CR; write it with LF endings")
        fenced = False
        for line in text.splitlines():
            if _FENCE.match(line):
                fenced = not fenced
                continue
            if fenced:
                continue
            match = _TOO_HIGH.match(line)
            if match:
                problems.append(
                    f"{path.name}: has a `{match.group(1)} ` heading. A fragment is the "
                    "prose that goes *under* a release heading; the heading is added at "
                    "assembly, and anything at this level splits the section being cut."
                )
                break
        for pointer in positional_pointers(text):
            problems.append(
                f'{path.name}: "{pointer}" points at another entry by position, and '
                "assembly orders entries by filename, which you cannot see from here. "
                'Name it by its subject instead: "see the NIST SP 800-171 entry in '
                'this release". If it points within this entry, name that: "the '
                'table in this entry".'
            )
    return problems


def assemble(paths: list[Path], version: str) -> str:
    """The new release section, heading included, ending with one blank line."""
    bodies = [p.read_text(encoding="utf-8").strip() for p in paths]
    return f"## {version}\n\n" + "\n\n".join(bodies) + "\n\n"


def insert(changelog_text: str, section: str) -> str:
    """`section` placed above the newest existing release heading."""
    match = RELEASE_HEADING.search(changelog_text)
    if not match:
        raise SystemExit("changelog_fragments: no `## <x.y.z>` heading found in CHANGELOG.md")
    return changelog_text[: match.start()] + section + changelog_text[match.start() :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="validate fragments and stop")
    parser.add_argument("--version", help="version to cut, e.g. 1.5.0")
    parser.add_argument("--dry-run", action="store_true", help="print, change nothing")
    args = parser.parse_args(argv or [])

    found = fragments()
    problems = check()

    # The size of what was examined, always — a check that found nothing and
    # a check that ran over nothing are the same output otherwise.
    print(f"changelog fragments: {len(found)} in {FRAGMENT_DIR.name}/")
    for path in found:
        print(f"    {path.name}")
    for problem in problems:
        print(f"  PROBLEM  {problem}")
    if problems:
        return 1

    if args.check:
        return 0
    if not args.version:
        parser.error("--version is required unless --check")
    if not found:
        print("Nothing to assemble: no fragments. Is this release intentionally silent?")
        return 1

    section = assemble(found, args.version)
    if args.dry_run:
        print(f"\n--- would insert above the newest release heading ---\n{section}")
        return 0

    CHANGELOG.write_text(
        insert(CHANGELOG.read_text(encoding="utf-8"), section), encoding="utf-8", newline="\n"
    )
    for path in found:
        path.unlink()
    print(
        f"\nWrote ## {args.version} from {len(found)} fragment(s) and removed them.\n"
        "Now READ the assembled section top to bottom and arrange it: this tool\n"
        "emits filename order and has no opinion about what belongs first. The\n"
        "entries that change someone's exit codes belong at the top."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

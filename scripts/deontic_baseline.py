"""The deontic conservation baseline: record it, compare against it, and
regenerate it only with every change printed (#376).

    python scripts/deontic_baseline.py           compare; print every changed
                                                 (document, reader, row); exit
                                                 1 if anything changed
    python scripts/deontic_baseline.py --write   the same printout, then write
                                                 the new baseline

The corpus is `tests/fixtures/deontic_corpus/`: generated Standards and
Procedures, read as `check` reads them, the body after `frontmatter` has
taken the frontmatter off. The baseline names the commit whose code recorded
it. `tests/test_deontic_conservation.py` compares against the committed file
and never recomputes it: a test that computes its own expectation agrees
with itself by construction (1d on #376).

**Regenerating is never a test flag.** A PR that changes `baseline.json`
carries this script's printout, so a reader rules on each change, as 80 did
on #376. A baseline diff with no printout is changes-requested on sight.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "fixtures" / "deontic_corpus"
BASELINE = CORPUS / "baseline.json"
READERS = (
    "analyze",
    "heading_statements",
    "playbook_obligations",
    "weakened_citations",
    "playbook_tagged_headings",
    "unresolved_playbook_parts",
    "binding_share",
    "unanchored",
)


def documents() -> list[Path]:
    """Every corpus document, in a fixed order. Named `*.md.txt`: they are data
    this test reads, not the repository's markdown, so the markdown gates
    (`mdformat` in `scripts/check.py` and CI) leave them as generated."""
    found = sorted((CORPUS / "documents").rglob("*.md.txt"))
    if not found:
        raise SystemExit(f"no documents under {CORPUS / 'documents'}")
    return found


def _row(statement) -> list:
    return [
        statement.line,
        statement.text,
        statement.modality,
        bool(statement.cited),
        list(statement.citations),
        bool(statement.heading),
    ]


def record() -> dict:
    """Every block-structure-dependent reader's output, per document."""
    import frontmatter

    from policyforge.content import deontic, grounding

    docs = {}
    for path in documents():
        body = frontmatter.loads(path.read_text(encoding="utf-8")).content
        docs[path.relative_to(CORPUS / "documents").as_posix()] = {
            "analyze": [_row(s) for s in deontic.analyze(body)],
            "heading_statements": [_row(s) for s in deontic.heading_statements(body)],
            "playbook_obligations": [_row(s) for s in deontic.playbook_obligations(body)],
            "weakened_citations": [_row(s) for s in deontic.weakened_citations(body)],
            "playbook_tagged_headings": [list(x) for x in deontic.playbook_tagged_headings(body)],
            "unresolved_playbook_parts": [list(x) for x in deontic.unresolved_playbook_parts(body)],
            "binding_share": list(deontic.binding_share(body)),
            "unanchored": [[u.claim.line, u.claim.text] for u in grounding.unanchored(body)],
        }
    return docs


def _commit_of_code() -> str:
    """The commit of the `policyforge` that was imported, with `+dirty` when
    its tree has changes: the baseline says which code produced it."""
    import policyforge

    source = Path(policyforge.__file__).resolve().parent
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(source), *a], capture_output=True, text=True, encoding="utf-8"
    )
    head = run("rev-parse", "HEAD").stdout.strip()
    dirty = run("status", "--porcelain", "--", ".").stdout.strip()
    return head + ("+dirty" if dirty else "")


def changes(old: dict, new: dict) -> list[str]:
    """Every changed (document, reader, row), as printable lines."""
    lines = []
    for doc in sorted(set(old) | set(new)):
        if doc not in old or doc not in new:
            sign, what = ("+", "added") if doc in new else ("-", "removed")
            lines.append(f"{sign} {doc}: document {what}")
            continue
        for reader in READERS:
            before, after = old[doc].get(reader), new[doc].get(reader)
            if before == after:
                continue
            if reader == "binding_share":
                lines.append(f"~ {doc} {reader}: {before} -> {after}")
                continue
            gone = [r for r in before if r not in after]
            came = [r for r in after if r not in before]
            for sign, rows in (("-", gone), ("+", came)):
                for r in rows:
                    shown = json.dumps(r[1:], ensure_ascii=False)[:240]
                    lines.append(f"{sign} {doc} {reader} line {r[0]}: {shown}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--write", action="store_true", help="write the new baseline after printing"
    )
    args = parser.parse_args(argv)
    # The printout goes into a PR, so it is UTF-8 wherever it is sent. On
    # Windows a redirected stdout otherwise takes the console codepage: the
    # printout for #376 came out cp1252 (a `§` as 0xA7), and a character
    # cp1252 lacks would have crashed it before a word of the diff printed.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    new = record()
    old = json.loads(BASELINE.read_text(encoding="utf-8"))["docs"] if BASELINE.exists() else {}
    printed = changes(old, new)
    for line in printed:
        print(line)
    print(f"{len(printed)} changed row(s) across {len(new)} document(s)")
    if args.write:
        payload = {"recorded_at": _commit_of_code(), "readers": list(READERS), "docs": new}
        BASELINE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
        )
        print(f"wrote {BASELINE.relative_to(ROOT)} (recorded at {payload['recorded_at']})")
        return 0
    return 1 if printed else 0


if __name__ == "__main__":
    sys.exit(main())

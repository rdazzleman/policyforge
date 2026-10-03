#!/usr/bin/env python3
"""Describe a licensed framework export's structure without reproducing it.

    python scripts/describe_export.py local_content/hitrust/export.xlsx
    python scripts/describe_export.py local_content/hitrust/export.csv --json fingerprint.json

Writing a loader for HITRUST CSF, GovRAMP or any other licensed catalog
needs the file's *shape*: which columns exist, which are ever empty, how
identifiers are formatted, how deeply requirements nest. It does not need
the control text, and the control text is the part the license is about.

So this prints the shape and never a cell. Values are reduced to character
skeletons — `01.a` becomes `99.a`, `AC-2` becomes `AA-9` — which pins an
identifier format exactly while carrying none of the content. Long prose
fields are described by length and by whether they contain newlines or
markup, never sampled.

The output is safe to paste into an issue, share with a collaborator, or
commit. What it is *for* is letting somebody write a parser against a file
they cannot send anywhere.

Refuses to read outside `local_content/` without --anywhere, because that
directory is the gitignored one and the habit is worth enforcing in the
tool rather than in a docstring.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

#: Above this, a column is prose rather than an identifier or a label, and
#: is described by statistics instead of by shape.
PROSE_CHARS = 60

#: How many distinct skeletons to report per column. Enough to show that a
#: column holds two or three formats; not so many that a rare value becomes
#: identifiable by its shape alone.
TOP_SHAPES = 6


def skeleton(value: str) -> str:
    """A value's character classes, with runs collapsed.

    `01.a` -> `9.a`, `AC-2` -> `A-9`, `11.b Media Handling` -> `9.a A a`.
    Digits, upper and lower case each collapse to one symbol, so the result
    shows the *format* and cannot be read back into the value.
    """
    out = []
    for char in value.strip():
        if char.isdigit():
            symbol = "9"
        elif char.isupper():
            symbol = "A"
        elif char.islower():
            symbol = "a"
        elif char.isspace():
            symbol = " "
        else:
            symbol = char
        if not out or out[-1] != symbol:
            out.append(symbol)
    return "".join(out)


def read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=True)
        sheet = book.active
        rows = [
            ["" if cell is None else str(cell) for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ]
        book.close()
    else:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = [list(row) for row in csv.reader(handle)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def describe_column(name: str, values: list[str]) -> dict:
    filled = [v for v in values if v.strip()]
    lengths = [len(v) for v in filled]
    column = {
        "name": name,
        "rows": len(values),
        "non_empty": len(filled),
        "always_present": len(filled) == len(values),
        "distinct": len(set(filled)),
        "max_length": max(lengths, default=0),
        "mean_length": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
    }
    if column["max_length"] > PROSE_CHARS:
        column["kind"] = "prose"
        column["contains_newlines"] = any("\n" in v for v in filled)
        column["contains_markup"] = any(re.search(r"<[a-z/]|\*\*|^\s*[-*]\s", v) for v in filled)
        column["looks_like_a_list"] = any(re.search(r"^\s*\(?[a-z0-9]\)", v, re.M) for v in filled)
    else:
        column["kind"] = "label"
        shapes = Counter(skeleton(v) for v in filled)
        column["shapes"] = [
            {"shape": shape, "count": count} for shape, count in shapes.most_common(TOP_SHAPES)
        ]
        column["distinct_shapes"] = len(shapes)
        # A column with few distinct values is a category; its cardinality
        # is structural, its values are not.
        column["is_categorical"] = 0 < column["distinct"] <= 40
    return column


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", type=Path, help="write the fingerprint here")
    parser.add_argument(
        "--anywhere",
        action="store_true",
        help="allow a path outside local_content/",
    )
    args = parser.parse_args()

    parts = {p.lower() for p in args.path.resolve().parts}
    if "local_content" not in parts and not args.anywhere:
        print(
            f"{args.path} is outside local_content/, which is the gitignored "
            f"directory licensed exports belong in. Move it there, or pass "
            f"--anywhere if you know what you are doing."
        )
        return 2

    if not args.path.exists():
        print(f"{args.path} does not exist.")
        return 2

    header, rows = read_rows(args.path)
    if not header:
        print(f"{args.path} has no rows.")
        return 1

    columns = [
        describe_column(name or f"column_{n}", [row[n] if n < len(row) else "" for row in rows])
        for n, name in enumerate(header)
    ]
    fingerprint = {
        "file": args.path.name,
        "rows": len(rows),
        "columns": columns,
    }

    print(f"{args.path.name}: {len(rows)} rows, {len(header)} columns\n")
    print(f"{'column':38} {'filled':>12}  {'distinct':>8}  {'maxlen':>6}  kind")
    for column in columns:
        filled = f"{column['non_empty']}/{column['rows']}"
        print(
            f"{column['name'][:37]:38} {filled:>12}  {column['distinct']:>8}  "
            f"{column['max_length']:>6}  {column['kind']}"
        )

    print("\nvalue shapes (character classes only, never values):")
    for column in columns:
        if column["kind"] != "label" or not column.get("shapes"):
            continue
        shapes = ", ".join(f"{s['shape']!r} x{s['count']}" for s in column["shapes"])
        print(f"  {column['name'][:34]:35} {shapes}")

    prose = [c for c in columns if c["kind"] == "prose"]
    if prose:
        print("\nprose columns:")
        for column in prose:
            flags = [
                key.replace("_", " ")
                for key in ("contains_newlines", "contains_markup", "looks_like_a_list")
                if column.get(key)
            ]
            print(
                f"  {column['name'][:34]:35} mean {column['mean_length']:>7} chars, "
                f"max {column['max_length']}" + (f"  [{', '.join(flags)}]" if flags else "")
            )

    if args.json:
        args.json.write_text(json.dumps(fingerprint, indent=2), encoding="utf-8")
        print(f"\nfingerprint written to {args.json} — no cell values in it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

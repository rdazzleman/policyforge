"""Which SP 800-53 ids changed substantively between Rev 4 and Rev 5 (#410).

A mapping written against an older revision names 800-53 ids whose meaning
may have moved. The AICPA's TSC-to-800-53 mapping is dated 2020-01-22, before
Rev 5 final. PolicyForge never remaps such an id silently; it flags it,
using the publisher's own statement of what changed (80's ruling 3 on #410).

**The source is NIST's own comparison workbook,**
`sp800-53r4-to-r5-comparison-workbook.xlsx`, a US government work, pinned
to our SHA-256 of the file we fetched. Its sheet "Rev4 Rev5 Compared" gives
each Rev 5 id a column headed "More than editorial or administrative
change? (Y/N)". `Y` is flagged with the label `CHANGED`; `N` is not. The
column is found by its heading, not its position, and any value other than
Y or N (in either case: NIST wrote two as lowercase `y`) stops the parse.

**Withdrawn ids** are the workbook's ids that the Rev 5 catalog does not
carry. NIST's OSCAL Rev 5 marks all 182 of them `withdrawn`: 90 the
workbook notes as "Withdrawn", and 92 that were already withdrawn before
Rev 5, whose change column reads N (ba, measured on #410).
"""

from __future__ import annotations

import hashlib
import io
import re

URL = (
    "https://csrc.nist.gov/files/pubs/sp/800/53/r5/upd1/final/docs/"
    "sp800-53r4-to-r5-comparison-workbook.xlsx"
)
#: Our SHA-256 of that file, fetched 2026-09-26 (ba, on #410).
SHA256 = "94465ffc5584e0c968342cfe0492f1c31b2854bed8b61914bf517651a5664570"

SHEET = "Rev4 Rev5 Compared"
_HEADING = "more than editorial or administrative change"
#: What a flagged mapping row says, in 80's words.
CHANGED = "changed between Rev 4 and Rev 5 (NIST)"
WITHDRAWN = "withdrawn in Rev 5 (NIST)"

#: The workbook as pinned (ba, measured on #410): 1,189 ids, 698 changed
#: substantively, 491 not. Exact in both directions, since the file is
#: pinned: a different count means the pin is wrong or the parse is.
EXTENT = (1189, 698)


class Rev4Rev5Error(ValueError):
    """The workbook is not the pinned one, or not shaped as it was."""


def require_pinned(raw: bytes) -> None:
    actual = hashlib.sha256(raw).hexdigest()
    if actual != SHA256:
        raise Rev4Rev5Error(
            f"NIST's Rev 4 to Rev 5 comparison workbook has SHA-256 {actual}, not the pinned "
            f"{SHA256}. Read the change before re-pinning."
        )


def parse(raw: bytes) -> dict[str, bool]:
    """{800-53 id: True if NIST marks its change more than editorial}."""
    import openpyxl

    require_pinned(raw)
    sheet = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)[SHEET]
    rows = list(sheet.iter_rows(values_only=True))
    header = rows[0]
    columns = [
        i
        for i, cell in enumerate(header)
        if cell and _HEADING in " ".join(str(cell).lower().split())
    ]
    if len(columns) != 1:
        raise Rev4Rev5Error(
            f"Found {len(columns)} columns headed {_HEADING!r} in {SHEET!r}; expected exactly one."
        )
    (flag_column,) = columns
    changed: dict[str, bool] = {}
    for row in rows[2:]:
        if not row or row[0] is None:
            continue
        control = re.sub(r"\s+", "", str(row[0]))
        value = str(row[flag_column] or "").strip().upper()
        if value not in ("Y", "N"):
            raise Rev4Rev5Error(f"{control}: change flag {row[flag_column]!r} is neither Y nor N.")
        if control in changed:
            raise Rev4Rev5Error(f"{control} appears twice in {SHEET!r}.")
        changed[control] = value == "Y"
    found = (len(changed), sum(changed.values()))
    if found != EXTENT:
        raise Rev4Rev5Error(
            f"Parsed {found[0]} ids, {found[1]} changed; the pinned workbook holds "
            f"{EXTENT[0]} and {EXTENT[1]}. Refusing."
        )
    return changed


def fetch() -> bytes:
    import requests

    response = requests.get(URL, timeout=120)
    response.raise_for_status()
    return response.content


def label(control: str, changed: dict[str, bool], rev5_ids: set[str]) -> str | None:
    """What to say beside a mapped 800-53 id, or None if nothing.

    `rev5_ids` is every control and enhancement id of the shipped Rev 5
    catalog. An id NIST's workbook lists that Rev 5 does not carry is
    withdrawn; one the workbook does not list at all is not a Rev 5 id, and
    the caller refuses it by name.
    """
    if control not in rev5_ids and control in changed:
        return WITHDRAWN
    if changed.get(control):
        return CHANGED
    return None

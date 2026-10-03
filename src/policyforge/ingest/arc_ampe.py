"""ARC-AMPE, and which of its two volumes actually holds the controls.

ARC-AMPE — Acceptable Risk Controls for ACA, Medicaid, and Partner Entities
— is CMS's security and privacy framework for Health Insurance Exchanges and
the entities around them. It supersedes and replaces MARS-E and the
Non-Exchange Entity GRC Framework, effective on publication. Published by
CMS, a federal agency, with no copyright notice or redistribution
restriction: a US government work, so it may be bundled here on the same
basis as NIST 800-53 and the HIPAA Security Rule.

## Volume I is not the catalog

Worth stating because the obvious source is the wrong one. ARC-AMPE ships in
two volumes and the one that turns up first in a search — `ARC-AMPE Volume
I`, a 55-page PDF — is the narrative: scope, applicability, roles, the
relationship to the ACA AE CSF Profile. It contains no controls. Volume I
says so itself, in a footnote it repeats four times: "ARC-AMPE Volume II is
the System Security and Privacy Plan (SSPP) template with required baseline
controls."

Volume II is an `.xlsx`, and it is what this module reads. A loader pointed
at Volume I would parse cleanly and find nothing, which is the failure mode
worth naming in a docstring rather than leaving for somebody to rediscover.

## What the workbook is

Volume II is an SSPP *template*, not a data export — most of its eight
sheets are blank grids for an entity to fill in. One sheet, `AE Mandatory
Baseline`, carries the catalog: 402 controls for an ACA Administering
Entity, each with CMS's own control statement, supplemental requirements and
guidance, and related controls. The remaining columns of that sheet — six
pairs of "Control Implementation Description" and "Control Status" — are the
blanks, and are deliberately not ingested. A schema field nothing reads is
clutter that reads as data.

Unlike the FedRAMP tailoring, this is a **real mandatory baseline**: every
row is required of an ACA AE, which is why `Control.baseline` is set here
and left unset there.

Two details of the text are load-bearing:

* **The control statements are already tailored.** Where 800-53 writes
  `[Assignment: organization-defined time period]`, ARC-AMPE writes "within
  twenty-four (24) hours" and "five (5) consecutive invalid logon attempts".
  CMS has made these decisions and the numbers are in the prose, not in a
  parameter table — so unlike a profile there is nothing to put in
  `parameter_values`, and folding the statements into anything else would
  lose the decided values.
* **96 of the 402 guidance cells are a placeholder**, reading "There are no
  supplemental control requirements and guidance for this control". That is
  the template saying nothing, not CMS saying something, so it is dropped to
  an empty `discussion` rather than carried into a generated document as if
  it were guidance.

## The DEE baseline is not public

CMS publishes a second Volume II for Direct Enrollment Entities, a smaller
308-control baseline. It is distributed through CMS zONE, which requires
requested access, so it is not fetched here and not bundled. `--export`
takes a local path for anyone who has it.

## Identifiers

The workbook writes `AC-01` and `AC-02(01)`, zero-padded. This codebase and
OSCAL write `AC-1` and `AC-2(1)`, and the identifier is the join key that
lets `policyforge map` crosswalk ARC-AMPE onto 800-53 — all 402 resolve
against the bundled Rev 5 catalog — so ids are normalized on the way in.
"""

from __future__ import annotations

import re

from .schema import Control, ControlEnhancement

#: The published revision this catalog is read from. CMS has no tags and no
#: API; the document's own version and date are what name a revision, and
#: both are carried in the filename of the artifact CMS links.
ARC_AMPE_VERSION = "v1.02"

#: The ACA Administering Entity workbook, as linked from CMS's Marketplace
#: regulations and guidance page. Volume I (the narrative PDF) is
#: deliberately not fetched — see the module docstring.
ARC_AMPE_URL = "https://www.cms.gov/files/document/arc-ampevol2sspp-aca-aev102-50803212025.xlsx"

#: What this baseline is required of. Set on every control because every row
#: of the sheet is mandatory for an ACA AE; this is a real selection, unlike
#: the FedRAMP tailoring in `fedramp.py`.
AE_BASELINE = "AE Mandatory"

#: The base catalog ARC-AMPE derives from, used as the crosswalk key so
#: `mapping/crosswalk.py` anchors each ARC-AMPE control on its 800-53
#: equivalent. Same convention and key as `govramp.py` and `fedramp.py`.
#:
#: ARC-AMPE is not a profile — CMS restates each control in its own words,
#: with its own parameter decisions baked in — but it numbers them with
#: 800-53 identifiers, and CMS describes the baseline as derived from SP
#: 800-53 Rev 5. That makes identity the right mapping and the publisher's
#: own claim, not this loader's inference.
#:
#: Recorded only for ids that resolve in the catalog passed to
#: `parse_arc_ampe`. Without one, no crosswalk is written at all: a mapping
#: onto a control nobody checked exists is the kind of quiet wrongness that
#: reaches an assessor as a citation.
NIST_SOURCE = "NIST 800-53"

#: Rows scanned at the top of a sheet when looking for its header. The
#: controls sheet opens with a title, an instruction paragraph and a
#: twenty-row family index before the header row, so this reaches well past
#: the handful a data export would need.
HEADER_SCAN_DEPTH = 60

#: Column captions that identify the controls sheet, mapped to the field
#: each fills. Matched on a normalized caption rather than a position, so a
#: later revision that inserts or reorders columns still reads.
COLUMN_CAPTIONS = {
    "control family": "family",
    "control number": "control_id",
    "control name": "title",
    "arc-ampe control": "statement",
    "arc-ampe supplemental control requirements & guidance": "guidance",
    "related controls": "related",
}

#: The three captions without which a sheet is not the controls sheet.
REQUIRED_FIELDS = ("control_id", "title", "statement")

#: Every recognized field a sheet may lack and still qualify — derived from
#: the caption table rather than listed, so a caption added to
#: `COLUMN_CAPTIONS` later is covered without anyone remembering this line.
#:
#: **An optional column whose caption fails to match does not fail; it reads
#: as empty on every row** (#265). `cell()` returns `""` both for a genuinely
#: blank cell and for a column that was never found, so from that point on
#: the two are indistinguishable. The caption is matched exactly after
#: lowercasing, so `&` -> `and` or `Related Controls` -> `Related Control(s)`
#: in a later revision drops the whole column in silence while the sheet
#: still qualifies on its three required captions. The loader cannot tell a
#: drifted caption from a column CMS removed, so it reports the absence loudly
#: and leaves the decision to whoever is re-pinning.
OPTIONAL_FIELDS = tuple(f for f in COLUMN_CAPTIONS.values() if f not in REQUIRED_FIELDS)

#: `AC-01`, `AC-02(01)`, tolerant of the stray space a hand-edited template
#: acquires.
_CONTROL_ID_RE = re.compile(r"^([A-Za-z]{2})-(\d{1,3})(?:\s*\(\s*(\d{1,3})\s*\))?$")

#: The template's way of saying a control has no supplemental guidance.
#: The sentence is retyped per row and the wording wobbles more than a
#: regex over the raw cell can follow. v1.02 writes "requirements **&**
#: guidance" in nineteen places, and `PE-2` reads "require**and**ents" —
#: a find-and-replace of "&" with "and" that ran through the middle of a
#: word. The trailing clause moves too: "at this time", "for this
#: control". All nineteen shipped their boilerplate as if it were content.
#:
#: **The sentinel must account for the whole sentence, not merely open
#: it.** Anchoring only at the start reintroduces the failure this is
#: written to prevent, one level down: "…guidance at this time, however
#: agencies shall document exceptions in the SSP" is a single sentence
#: whose opening matches, and a prefix match would discard the binding
#: half with it. Found by policyforge-b5 in review, latent rather than
#: live — v1.02 has nineteen sentinel-bearing fields and none carries
#: extra content in the same sentence.
#:
#: The trailing clause moves ("at this time", "for this control"), so it
#: is admitted as a *shape* — one prepositional phrase — rather than as a
#: list of endings that would need maintaining. Prepositions are a closed
#: class; trailing clauses are not.
#:
#: **The known trade, named by b5 rather than discovered later.** That
#: slot cannot tell a closing clause from a pointer: "…and guidance **in
#: the attached appendix**" is structurally identical to "…at this time"
#: and would be emptied, though it means the guidance lives elsewhere.
#: No cell in v1.02 does this. It is the price of admitting a shape
#: instead of enumerating endings, and the alternative — a list of
#: permitted endings — was rejected because it silently drops whatever
#: the next CMS revision spells differently, which is the defect this
#: whole function exists to undo.
_NO_GUIDANCE_RE = re.compile(
    r"there\s+(?:are|is)\s+no\s+supplemental\s+control\s+"
    r"require\w*\s+(?:and\s+)?guidance"
    r"(?:\s+(?:at|for|in|on|to|under|within)\s+(?:\w+\s+){0,2}\w+)?",
)


def _clean(value: object) -> str:
    """Cell text with the template's typography left alone.

    Non-breaking spaces become ordinary ones and trailing whitespace goes,
    because those are artifacts of editing in Excel. Bullets, curly quotes
    and section signs stay: they are how CMS structures and punctuates
    normative prose, and flattening them would rewrite the text this project
    exists to quote faithfully.
    """
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _normalize_caption(value: object) -> str:
    return " ".join(_clean(value).lower().split())


def normalize_control_id(value: str) -> tuple[str, str]:
    """`("AC-1", "")` for a control, `("AC-2", "AC-2(1)")` for an enhancement.

    Normalized to the spelling the OSCAL loader produces, because the id is
    what joins ARC-AMPE to the catalog it derives from. An unparseable id is
    kept verbatim as a base id rather than dropped — a row that reached here
    is a real row of the baseline, and a mandatory control missing from a
    scope report is worse than one with an odd identifier.
    """
    match = _CONTROL_ID_RE.match(_clean(value))
    if match is None:
        return " ".join(_clean(value).split()), ""
    family, number, enhancement = match.groups()
    base = f"{family.upper()}-{int(number)}"
    return (base, f"{base}({int(enhancement)})") if enhancement else (base, "")


def split_related(value: str) -> list[str]:
    """`"IA-1, PM-9, PS-8, SI-12 "` -> `["IA-1", "PM-9", "PS-8", "SI-12"]`.

    The cell is free text and says "None." as often as it lists anything, so
    tokens are matched rather than split: a separator-splitting parse yields
    a related control called "None" and a trailing empty string, both of
    which then travel into generated documents.
    """
    text = _clean(value)
    if not text or text.lower().rstrip(".") == "none":
        return []
    found: list[str] = []
    for token in re.findall(r"[A-Za-z]{2}-\d{1,3}(?:\s*\(\s*\d{1,3}\s*\))?", text):
        base, enhancement = normalize_control_id(token)
        identifier = enhancement or base
        if identifier and identifier not in found:
            found.append(identifier)
    return found


def _says_no_guidance(sentence: str) -> bool:
    """Is this one sentence *entirely* CMS's "there is nothing here" boilerplate?

    `fullmatch`, not `match`. A prefix match is satisfied by the opening
    and lets the sentence continue into real obligation — which is the
    failure a raw search invites, arriving one level below the per-sentence
    split that was supposed to stop it.

    Two things here are **deliberately redundant**, and a mutation sweep
    will report both as surviving. Spelling "&" out is also achieved by
    stripping punctuation to a space, because the pattern's "and" is
    optional; and the `bare` guard is unreachable because `_guidance`
    drops empty fragments and the pattern cannot match "" anyway. No
    input distinguishes either — they are equivalent mutants rather than
    coverage gaps, which is a different thing from a survivor the corpus
    merely fails to exercise. Kept because each states an intent the
    other only implies.
    """
    bare = " ".join(re.sub(r"[^\w\s]", " ", sentence.replace("&", " and ")).split())
    return bool(bare) and _NO_GUIDANCE_RE.fullmatch(bare.lower()) is not None


def _guidance(value: str) -> str:
    """The cell's guidance, or `""` when it says only that there is none.

    **The test is whether the cell says nothing else, not whether the
    sentinel appears in it.** Searching the raw text fails in both
    directions, and ARC-AMPE v1.02 contains one of them: nineteen fields
    spell the sentinel with "&", did not match, and shipped a sentence
    meaning "there is nothing here" as content. The opposite failure is
    the one a search invites — a cell that genuinely discusses
    supplemental control requirements would be discarded whole.

    Every sentence must be the boilerplate *in full* — see
    `_says_no_guidance` for why "in full" is the load-bearing half — so
    one real sentence beside it keeps the cell, and the trailing clause
    may move without anyone maintaining a list of endings.

    **The splitter is deliberately crude, and its crudeness points the
    safe way.** `[.;]` over-splits on abbreviations and decimals, which
    cost `entail/base.py` 19 of 43 findings. Here an over-split produces
    a fragment that cannot match the sentinel in full, `all()` goes
    False, and the cell is **kept**. Losing real guidance is silent and
    unrecoverable in a generated document; keeping boilerplate is
    visible and caught by the catalog guard. Anyone tempted to make this
    splitter cleverer should check which way its new failure points.

    A control carrying guidance that says it has no guidance is the
    failure this project keeps finding rather than a hypothetical one:
    it counts, it renders into a policy document, and it means nothing.
    """
    text = _clean(value)
    sentences = [s for s in re.split(r"[.;]", text) if s.strip()]
    if sentences and all(_says_no_guidance(s) for s in sentences):
        return ""
    return text


def detect_columns(header: list[object]) -> dict[str, int]:
    """`{field: column index}` for every caption this sheet recognizes."""
    found: dict[str, int] = {}
    for index, caption in enumerate(header):
        field = COLUMN_CAPTIONS.get(_normalize_caption(caption))
        if field and field not in found:
            found[field] = index
    return found


def _control_rows_below(sheet, columns: dict[str, int], data_start: int) -> int:
    """How many rows below a candidate header carry a real control number.

    This is the discriminator, and it is not optional. Captions alone pick
    the wrong sheet: `Instructional Guidance` walks an entity through filling
    the template by reproducing the controls header verbatim over a
    three-row worked example, so it matches every caption the real sheet
    does and matches them earlier in the workbook. Counting control-shaped
    ids separates a 402-row catalog from a 3-row illustration of one, which
    is the difference the captions cannot see.
    """
    index = columns.get("control_id")
    if index is None:
        return 0
    count = 0
    for row in sheet.iter_rows(min_row=data_start, values_only=True):
        if index < len(row) and _CONTROL_ID_RE.match(_clean(row[index])):
            count += 1
    return count


def find_controls_sheet(workbook) -> tuple[str, dict[str, int], int]:
    """`(sheet title, columns, first data row)` for the baseline sheet.

    Found by its shape rather than by name or position, the same way
    `govramp_export.py` finds its sheet among a template's other thirteen.
    The AE and DEE workbooks name their sheets differently ("AE Mandatory
    Baseline"), and a later revision may rename or renumber again; what has
    stayed put is a header naming these columns with several hundred
    control-numbered rows underneath it.

    Ranked on that row count, with the caption count breaking ties, because
    a sheet can carry the whole header and almost no catalog — see
    `_control_rows_below`.
    """
    best: tuple[int, int, str, dict[str, int], int] | None = None
    for sheet in workbook.worksheets:
        # Bounded by the sheet's own extent as well as the scan depth. Asking
        # a writable worksheet for rows past its last one does not return
        # nothing — openpyxl materializes them, so the sheet afterwards
        # reports a `max_row` of HEADER_SCAN_DEPTH and every later read walks
        # dozens of empty rows this function invented.
        depth = min(HEADER_SCAN_DEPTH, sheet.max_row or 0)
        for index, row in enumerate(
            sheet.iter_rows(min_row=1, max_row=depth, values_only=True), start=1
        ):
            columns = detect_columns(list(row))
            if not all(field in columns for field in REQUIRED_FIELDS):
                continue
            data_start = index + 1
            rank = (_control_rows_below(sheet, columns, data_start), len(columns))
            if rank[0] and (best is None or rank > (best[0], best[1])):
                best = (rank[0], rank[1], sheet.title, columns, data_start)

    if best is None:
        captions = ", ".join(sorted(COLUMN_CAPTIONS))
        raise ValueError(
            "No ARC-AMPE controls sheet found. Looked in the first "
            f"{HEADER_SCAN_DEPTH} rows of every sheet for a header naming at least "
            f"'Control Number', 'Control Name' and 'ARC-AMPE CONTROL', with "
            f"control-numbered rows beneath it (recognized captions: {captions}). "
            "If this is ARC-AMPE Volume I, it is the narrative PDF and holds no "
            "controls — Volume II is the workbook. Otherwise `policyforge "
            "generate-parser --framework arc-ampe --sample <path>` drafts a loader "
            "for this file's shape."
        )

    _, _, title, columns, data_start = best
    return title, columns, data_start


class Summary:
    """What a run found, for a report a reviewer can check against CMS's own
    published figure — 402 controls for an ACA AE."""

    def __init__(self) -> None:
        self.sheet = ""
        self.controls = 0
        self.enhancements = 0
        self.with_guidance = 0
        self.with_related = 0
        #: Optional fields whose caption was not found in the header. Every
        #: row reads empty for these, so they are reported by name rather
        #: than left to surface as a count of zero.
        self.missing_optional: list[str] = []
        self.rows_skipped = 0
        self.unparsed: list[str] = []
        self.crosswalked = 0
        self.unresolved: list[str] = []

    def format_report(self) -> list[str]:
        lines = [
            f"Read {self.controls} controls and {self.enhancements} enhancements "
            f"({self.controls + self.enhancements} baseline items) "
            f"from sheet '{self.sheet}'.",
            f"{self.with_guidance} carry supplemental requirements and guidance.",
            f"{self.with_related} controls list related controls.",
        ]
        # First and loud, because the lines above read as facts about the
        # source and are not: with the column missing, "0 carry" measures the
        # parse rather than CMS, and the related count used to print nothing
        # at all.
        lines[:0] = [
            f"WARNING: no '{field}' column was found in the header, so every row "
            f"reads it as empty. A caption that no longer matches exactly drops "
            f"the whole column without failing the parse."
            for field in self.missing_optional
        ]
        if self.crosswalked:
            lines.append(f"Crosswalked {self.crosswalked} onto their 800-53 equivalents.")
        if self.unresolved:
            shown = ", ".join(self.unresolved[:8])
            more = f" (+{len(self.unresolved) - 8} more)" if len(self.unresolved) > 8 else ""
            lines.append(f"Not found in the 800-53 catalog, so left uncrosswalked: {shown}{more}.")
        if self.rows_skipped:
            lines.append(f"Skipped {self.rows_skipped} non-empty rows with no control number.")
        if self.unparsed:
            shown = ", ".join(self.unparsed[:8])
            more = f" (+{len(self.unparsed) - 8} more)" if len(self.unparsed) > 8 else ""
            lines.append(f"Unrecognized control ids kept verbatim: {shown}{more}.")
        return lines


def parse_arc_ampe(
    workbook,
    *,
    version: str = ARC_AMPE_VERSION,
    nist_ids: set[str] | None = None,
) -> tuple[list[Control], Summary]:
    """The ARC-AMPE mandatory baseline, from an open Volume II workbook.

    Rows arrive in catalog order and enhancements follow their parent, so a
    parent is created on first sight and enhancements attach to it. A parent
    that ARC-AMPE does not state in its own right — the baseline includes
    `AC-3(8)` without `AC-3`, for instance — still gets a Control to hold it,
    carrying no statement, because `Control.enhancements` is the only
    container this schema has.

    `nist_ids` is every identifier the 800-53 catalog defines. Pass it and
    each ARC-AMPE control that matches one is crosswalked onto it; omit it
    and no crosswalk is written, which leaves the catalog usable on its own
    but invisible to `policyforge map`. See `NIST_SOURCE`.
    """
    sheet_title, columns, data_start = find_controls_sheet(workbook)
    sheet = workbook[sheet_title]

    summary = Summary()
    summary.sheet = sheet_title
    summary.missing_optional = [f for f in OPTIONAL_FIELDS if f not in columns]
    assembled: dict[str, Control] = {}
    order: list[str] = []

    def cell(row: tuple, field: str) -> str:
        index = columns.get(field)
        if index is None or index >= len(row):
            return ""
        return _clean(row[index])

    # Counted in a pass of its own, deliberately. The first version of this
    # incremented alongside the parse loop below, which made the comparison
    # a tautology: a loop that stopped early decremented both sides and the
    # identity still held. Truncating the scan dropped the catalog from 215
    # controls to 103 in silence. An independent pass is what makes the
    # check capable of failing.
    rows_with_content = sum(
        1
        for row in sheet.iter_rows(min_row=data_start, values_only=True)
        if any(_clean(value) for value in row)
    )

    rows_consumed = 0
    for row in sheet.iter_rows(min_row=data_start, values_only=True):
        raw_id = cell(row, "control_id")
        if raw_id:
            rows_consumed += 1
        if not raw_id:
            # Only rows carrying something are worth reporting. The sheet is
            # padded with blanks and broken up by section bands, and counting
            # those as skipped buries the one case a reader should look at —
            # a row with real text whose control number went missing.
            if any(_clean(value) for value in row):
                summary.rows_skipped += 1
            continue

        base_id, enhancement_id = normalize_control_id(raw_id)
        if not _CONTROL_ID_RE.match(raw_id):
            summary.unparsed.append(raw_id)

        title = cell(row, "title")
        statement = cell(row, "statement")
        guidance = _guidance(cell(row, "guidance"))
        related = split_related(cell(row, "related"))
        if guidance:
            summary.with_guidance += 1

        identifier = enhancement_id or base_id
        crosswalk: dict[str, str] = {}
        if nist_ids is not None:
            if identifier in nist_ids:
                crosswalk = {NIST_SOURCE: identifier}
                summary.crosswalked += 1
            else:
                summary.unresolved.append(identifier)

        parent = assembled.get(base_id)
        if parent is None:
            parent = Control(
                control_id=base_id,
                title="",
                framework="ARC-AMPE",
                framework_version=version,
                family=cell(row, "family") or None,
                family_abbr=base_id.split("-")[0] if "-" in base_id else None,
                baseline=AE_BASELINE,
            )
            assembled[base_id] = parent
            order.append(base_id)

        if not enhancement_id:
            parent.title = title
            parent.control_statement = statement
            parent.discussion = guidance
            parent.related_controls = related
            if related:
                summary.with_related += 1
            parent.source_crosswalk = crosswalk
            summary.controls += 1
            continue

        parent.enhancements.append(
            ControlEnhancement(
                enhancement_id=enhancement_id,
                title=title,
                baseline=AE_BASELINE,
                description=statement,
                additional_requirements=guidance,
                source_crosswalk=crosswalk,
            )
        )
        summary.enhancements += 1

    controls = [assembled[key] for key in order]
    _require_every_row(summary, consumed=rows_consumed, with_content=rows_with_content)
    return controls, summary


def _require_every_row(summary: Summary, *, consumed: int, with_content: int) -> None:
    """Refuse a parse that lost a row the sheet contains.

    **Rows read plus rows skipped must equal rows carrying content.**
    Every row in the baseline is either consumed or reported as skipped;
    silently absent is the outcome this makes impossible.

    **Counted in rows, not in emitted items**, and the difference is not
    cosmetic. A control stated only as an enhancement gets a parent
    synthesised for it, and that parent has no row — so emitted items can
    legitimately exceed rows read. The first version of this check
    compared against emitted items; it reconciled exactly against CMS's
    published workbook, where every parent happens to have its own row,
    and broke six tests built on sheets where one does not. A
    relationship that holds on the shipped data and not in general is
    the kind of thing a real corpus cannot tell you is wrong.

    **The failure it guards is the one this loader has already had.** A
    caption-matched header can select the wrong sheet — the
    `Instructional Guidance` decoy matches every caption and matches
    *earlier* in the workbook — and the result is a plausible
    three-control catalog rather than an error. That case is caught by
    name today; this catches the general form, including a row scan that
    stops early or a column that moves.

    Counted against the **sheet**, not a constant. `rows_with_content` is
    gathered without consulting the control-id column, so both sides come
    from the workbook being read and CMS may publish a longer baseline
    without anyone editing a number here. The same argument
    `part2_loader._require_sections` makes for eCFR.
    """
    if consumed + summary.rows_skipped != with_content:
        raise ValueError(
            f"the sheet has {with_content} row(s) carrying content; {consumed} "
            f"were read and {summary.rows_skipped} reported skipped, leaving "
            f"{with_content - consumed - summary.rows_skipped} unaccounted for. "
            f"A row was neither read nor reported, which usually means the header "
            f"matched the wrong sheet or a column moved."
        )


def load_workbook_from_bytes(content: bytes):
    """Open an `.xlsx` held in memory.

    Read-only and values-only: the template carries formulas and a great
    deal of formatting, none of which is wanted, and streaming keeps a
    multi-megabyte workbook from being materialized twice.
    """
    import io

    from openpyxl import load_workbook

    return load_workbook(io.BytesIO(content), read_only=True, data_only=True)


def fetch_arc_ampe(*, url: str = ARC_AMPE_URL) -> bytes:
    """The Volume II workbook, as CMS publishes it.

    Returns bytes rather than an open workbook so the caller can hash
    exactly what was fetched — provenance over the artifact, not over a
    re-serialization of it.
    """
    import requests

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content


__all__ = [
    "AE_BASELINE",
    "ARC_AMPE_URL",
    "ARC_AMPE_VERSION",
    "Summary",
    "detect_columns",
    "fetch_arc_ampe",
    "find_controls_sheet",
    "load_workbook_from_bytes",
    "normalize_control_id",
    "parse_arc_ampe",
    "split_related",
]

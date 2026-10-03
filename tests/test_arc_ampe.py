"""ARC-AMPE Volume II, and finding the catalog inside an SSPP template.

The workbooks built here are invented, but their awkward shape is copied
from CMS's: a controls sheet that opens with a title, an instruction
paragraph and a twenty-row family index before its header; a separate
`Instructional Guidance` sheet that reproduces that same header over a
three-row worked example; six pairs of blank implementation columns to the
right of the data; and a guidance cell that says, in words, that there is no
guidance.

That last pair is the point of most of these tests. Captions alone pick the
wrong sheet — the decoy matches every one of them, and matches earlier in
the workbook — which is a failure that produces a plausible three-control
catalog rather than an error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.ingest import arc_ampe
from policyforge.ingest.schema import load_controls

CATALOG = Path("data/frameworks/arc-ampe/controls.json")

HEADER = [
    "#",
    "Control Family",
    "Control Number",
    "Control Name",
    "ARC-AMPE CONTROL",
    "ARC-AMPE SUPPLEMENTAL CONTROL REQUIREMENTS & GUIDANCE",
    "Related Controls",
    "MASTER / OVERALL Control Status",
    "Control Implementation Description",
]

NO_GUIDANCE = "•  There are no supplemental control requirements and guidance for this control."


def _sheet(workbook, title: str, rows: list[list[object]], *, preamble: int = 0):
    sheet = workbook.create_sheet(title)
    for _ in range(preamble):
        sheet.append(["Access Control (AC)"])
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    return sheet


def _row(number: int, cid: str, name: str, statement: str, guidance: str, related: str):
    return [number, "Access Control", cid, name, statement, guidance, related, None, None]


@pytest.fixture
def workbook():
    """A template shaped like CMS's: a decoy sheet first, the catalog second."""
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)

    # The decoy. Same header, three rows, and it comes first in the book.
    _sheet(
        book,
        "Instructional Guidance",
        [_row(1, "AC-01", "Example", "Example statement.", NO_GUIDANCE, "None.")],
    )
    _sheet(
        book,
        "AE Mandatory Baseline",
        [
            _row(1, "AC-01", "Policy", "a. Develop.", "•  Define roles.", "IA-1, PM-9"),
            _row(2, "AC-02", "Account Management", "a. Define.", NO_GUIDANCE, "None."),
            _row(3, "AC-02(01)", "Automated Management", "Support.", "•  Use automation.", "AC-2"),
            _row(4, "AC-03(08)", "Revocation", "Enforce.", NO_GUIDANCE, ""),
            # A section band: real text, no control number. Worth reporting.
            [None, "Awareness and Training", None, None, None, None, None, None, None],
            # Padding. Not worth reporting.
            [None, None, None, None, None, None, None, None, None],
        ],
        preamble=20,
    )
    return book


# --------------------------------------------------------------------------
# Finding the sheet
# --------------------------------------------------------------------------


def test_the_catalog_wins_over_a_decoy_with_the_same_header(workbook):
    """`Instructional Guidance` matches every caption the real sheet does.

    It also appears first, so a caption-scored search picks it and returns a
    three-control catalog that looks entirely reasonable. Rows carrying a
    control number are what separate a catalog from an illustration of one.
    """
    title, columns, data_start = arc_ampe.find_controls_sheet(workbook)

    assert title == "AE Mandatory Baseline"
    assert columns["control_id"] == 2
    assert data_start == 22, "header sits below the twenty-row family index"


def test_a_workbook_with_no_controls_sheet_says_which_captions_it_wanted():
    from openpyxl import Workbook

    book = Workbook()
    book.active.append(["Version", "Date", "Author"])

    with pytest.raises(ValueError, match="Control Number"):
        arc_ampe.find_controls_sheet(book)


def test_the_error_names_volume_i_as_the_likely_mistake():
    """The obvious source is the wrong one, so the error says so.

    Volume I is the narrative PDF and holds no controls; somebody who
    downloaded it and got an unhelpful parse error would have no way to
    learn that from the failure.
    """
    from openpyxl import Workbook

    book = Workbook()
    with pytest.raises(ValueError, match="Volume I"):
        arc_ampe.find_controls_sheet(book)


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


def test_enhancements_attach_to_the_control_they_enhance(workbook):
    controls, summary = arc_ampe.parse_arc_ampe(workbook)
    by_id = {c.control_id: c for c in controls}

    assert [c.control_id for c in controls] == ["AC-1", "AC-2", "AC-3"]
    assert [e.enhancement_id for e in by_id["AC-2"].enhancements] == ["AC-2(1)"]
    assert summary.controls == 2
    assert summary.enhancements == 2


def test_a_control_stated_only_as_an_enhancement_still_gets_a_parent(workbook):
    """The baseline includes AC-3(8) without AC-3.

    `Control.enhancements` is the only container the schema has, so the
    parent exists and carries no statement — which is the truth about it.
    """
    controls, _ = arc_ampe.parse_arc_ampe(workbook)
    parent = {c.control_id: c for c in controls}["AC-3"]

    assert parent.control_statement == ""
    assert [e.enhancement_id for e in parent.enhancements] == ["AC-3(8)"]


def test_the_template_saying_there_is_no_guidance_becomes_no_guidance(workbook):
    """That sentence is the template speaking, not CMS.

    Carried through, it reaches a generated document as if it were
    supplemental guidance CMS wrote.
    """
    controls, summary = arc_ampe.parse_arc_ampe(workbook)
    by_id = {c.control_id: c for c in controls}

    assert by_id["AC-2"].discussion == ""
    assert by_id["AC-1"].discussion == "•  Define roles."
    assert summary.with_guidance == 2


@pytest.mark.parametrize(
    "wording",
    [
        "•  There are no supplemental control requirements and guidance for this control.",
        "•  There are no supplemental control requirements & guidance at this time.",
        "•  There are no supplemental control requireandents and guidance at this time.",
        "There is no supplemental control requirement and guidance.",
        "THERE ARE NO SUPPLEMENTAL CONTROL REQUIREMENTS & GUIDANCE.",
    ],
    ids=["and", "ampersand", "corrupted-word", "singular", "shouting"],
)
def test_every_spelling_of_the_boilerplate_becomes_no_guidance(wording: str):
    """**v1.02 shipped nineteen fields that this used to miss.**

    The sentence is retyped per row and drifts. Eighteen write "&" where
    the pattern wanted "and"; `PE-2` reads "requireandents", a
    find-and-replace of "&" that ran through the middle of the word. Each
    one was kept and rendered into documents as if CMS had written it.

    `corrupted-word` is not hypothetical tidiness — it is the exact
    string in the published workbook.
    """
    assert arc_ampe._guidance(wording) == ""


def test_guidance_that_merely_mentions_the_phrase_survives():
    """**The opposite error, which searching the cell invites.**

    A row whose guidance genuinely discusses supplemental control
    requirements must not be discarded because the sentinel appears
    inside it. The rule is that every sentence is boilerplate, so one
    real sentence beside it keeps the whole cell.
    """
    cell = (
        "Agencies must document supplemental control requirements and guidance "
        "annually. There are no supplemental control requirements and guidance "
        "for sub-part (b)."
    )

    assert arc_ampe._guidance(cell) == cell


@pytest.mark.parametrize(
    "cell",
    [
        "There are no supplemental control requirements and guidance at this time, "
        "however agencies shall document exceptions in the SSP.",
        "There is no supplemental control requirement and guidance - agencies must "
        "still record the decision.",
    ],
    ids=["comma-continuation", "dash-continuation"],
)
def test_an_obligation_inside_the_sentinel_sentence_survives(cell: str):
    """**The version of the test above that is actually hard.**

    Found by policyforge-b5 in review of this PR. The test above passes
    against a *prefix* match too, because its fixture is two sentences,
    so `all()` is already False before anchoring matters. These are the
    same hazard in **one** sentence: the boilerplate opens it and binding
    guidance finishes it.

    With `re.match` the opening consumed the rest and the obligation was
    discarded silently and whole — the precise failure `_guidance`'s
    docstring claims to prevent, reintroduced one level below where the
    per-sentence split operates.

    Latent, not live: v1.02 has nineteen sentinel-bearing fields and none
    carries extra content in the same sentence. This pins the property so
    the next CMS revision cannot introduce one unnoticed.
    """
    assert arc_ampe._guidance(cell) == cell


def test_a_count_of_requirements_is_not_the_absence_of_them():
    """**"There are three…" is not "There are no…".**

    The sentinel's own clause with the negation swapped for a count, and
    it is a sentence CMS could plausibly write. A pattern that accepted
    any word where `no` belongs would empty it — destroying real
    guidance — while still emptying both genuine sentinel spellings, so
    it passes every other test in this file *and* the real-cell fixture.

    Found by policyforge-b5, correcting me. I had measured that mutant
    surviving and recorded it as "unobservable on v1.02 rather than
    untested". That was wrong: it is observable by exactly the mechanism
    I had just established two paragraphs earlier — the corpus cannot
    guard the destructive direction, hand-written cases can, and this is
    a destructive-direction mutant. **I documented a hole that one line
    fills.**
    """
    cell = "There are three supplemental control requirements and guidance for this control."

    assert arc_ampe._guidance(cell) == cell


def test_a_cell_that_says_it_twice_is_still_empty():
    """**What the per-sentence split is actually for.**

    Every sentinel cell in v1.02 is a single sentence — 0 of 96 are
    not — so the split is unexercised by real data, and removing it
    entirely passed the whole file including the real-cell fixture.
    Found by re-running the mutant sweep after closing b5's finding,
    and closed the same way rather than recorded as "unobservable":
    a corpus cannot guard what it does not contain, so the hand-written
    case is the guard.

    Two boilerplate sentences still mean no guidance. Treating the cell
    as one string makes `fullmatch` fail on the pair and keeps it,
    shipping the boilerplate this function exists to remove.
    """
    cell = (
        "There are no supplemental control requirements and guidance at this time. "
        "There is no supplemental control requirement and guidance for this control."
    )

    assert arc_ampe._guidance(cell) == ""


GUIDANCE_CELLS = Path(__file__).parent / "fixtures" / "arc_ampe_guidance_cells.json"


def test_real_workbook_cells_empty_exactly_the_sentinel_ones():
    """**The destructive direction, which nothing else guards.**

    Three things were true before this test and only two were checked:
    the catalog guard proves no sentinel *survived*, and the loader tests
    prove `_guidance` is right on hand-written strings. **Nothing proved
    no real guidance was emptied** — and that is the direction this
    change moves, and the direction whose failure is silent, because a
    document simply renders without the obligation.

    Designed by policyforge-b5 in review, after I offered a worse
    version that fetched CMS's workbook over the network: that would be
    muted the first week the URL moved, which is the failure
    `test_info_blocking_loader.py`'s docstring warns about. Cells, not
    the workbook. No network, no URL to rot.

    The fixture is real cells lifted from CMS's published v1.02: every
    sentinel cell, plus genuine guidance taken on a fixed stride rather
    than hand-picked — choosing the interesting ones is how the original
    defect survived review in the first place. The stride caught one
    that matters more than anything I would have chosen: a cell opening
    "Note: Ensure that all three bullets of this Supplemental Control
    Requirements & Guidance are reviewed", which mentions the sentinel's
    words and is binding guidance.

    **What this catches, measured rather than intended.** Mutating the
    pattern back to the original `requirements and guidance` takes the
    count from 96 to 77 and fails here — that is this test's unique
    contribution, and no other test in this file catches it.

    **What it does not catch, stated because the name suggests
    otherwise.** It does not independently guard the destructive
    direction. Broadening the pattern (`any` for `all`, prefix
    anchoring, a wider trailing slot) leaves every cell here untouched,
    because no genuine cell in CMS's corpus sits close enough to the
    sentinel to be swallowed — the "Note:" cell is the nearest and does
    not open with "there are no". Those mutants are caught by the
    hand-written one-sentence tests above, and **real data cannot
    replace them**: a corpus only exercises the failures it happens to
    contain.

    Nothing in v1.02 distinguishes a pattern that accepts any word where
    `no` belongs, so that mutant is invisible *here* — and it is caught
    by `test_a_count_of_requirements_is_not_the_absence_of_them`, a
    hand-written case, which is this docstring's own point made
    concrete.
    """
    cells = json.loads(GUIDANCE_CELLS.read_text(encoding="utf-8"))
    emptied = [c["control_id"] for c in cells if arc_ampe._guidance(c["guidance"]) == ""]
    kept = [c for c in cells if arc_ampe._guidance(c["guidance"]) != ""]

    assert len(emptied) == 96, "the count of cells CMS's v1.02 marks as having no guidance"
    assert all(c["sentinel"] for c in cells if c["control_id"] in emptied)
    assert [c["control_id"] for c in kept] == [c["control_id"] for c in cells if not c["sentinel"]]
    for cell in kept:
        assert arc_ampe._guidance(cell["guidance"]) == arc_ampe._clean(cell["guidance"])


def test_bullets_and_curly_quotes_survive_but_editing_artifacts_do_not():
    """CMS structures normative prose with bullets; Excel leaves NBSPs."""
    assert arc_ampe._clean("•  Text\xa0here  \n\n") == "•  Text here"


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("IA-1, PM-9, PS-8, SI-12 ", ["IA-1", "PM-9", "PS-8", "SI-12"]),
        ("None.", []),
        ("", []),
        ("AC-02(01); AC-17", ["AC-2(1)", "AC-17"]),
        ("AC-2, AC-2", ["AC-2"]),
    ],
)
def test_related_controls_are_matched_not_split(cell, expected):
    """Splitting on separators yields a related control called "None".

    The cell is free text and says "None." as often as it lists anything, so
    tokens are matched instead — which also normalizes `AC-02(01)` on the way
    through, since these become citations.
    """
    assert arc_ampe.split_related(cell) == expected


def test_only_rows_with_content_count_as_skipped(workbook):
    """A padded blank is not a dropped row, and reporting it as one buries
    the case that matters: real text whose control number went missing."""
    _, summary = arc_ampe.parse_arc_ampe(workbook)
    assert summary.rows_skipped == 1


def test_every_row_with_content_is_read_or_reported(workbook):
    """**External extent: rows read plus rows skipped equals rows with
    content.** Every other test here asks whether an entry is right; this
    is the only one asking whether they are all here.

    The failure it guards has already happened to this loader once — a
    caption-matched header selecting the `Instructional Guidance` decoy
    produces a plausible three-control catalog rather than an error. That
    case is caught by name; this catches the general form, including a
    row scan that stops early or a column that moves.
    """
    controls, summary = arc_ampe.parse_arc_ampe(workbook)
    consumed = len(controls) + sum(len(c.enhancements) for c in controls)

    # Not an equality against `consumed`: a control stated only as an
    # enhancement gets a parent synthesised, and that parent has no row.
    assert summary.rows_skipped == 1
    assert consumed >= 1


def test_a_row_that_is_neither_read_nor_reported_raises():
    """The guard's contract, asserted directly.

    **The first version of this check compared emitted items against
    rows** and reconciled exactly against CMS's published workbook —
    where every parent happens to have its own row. It broke six tests
    built on sheets where one does not. A relationship that holds on the
    shipped corpus and not in general is precisely what real data cannot
    tell you is wrong, so the check counts rows and the test says so.
    """
    summary = arc_ampe.Summary()
    summary.rows_skipped = 2

    with pytest.raises(ValueError, match=r"unaccounted for"):
        arc_ampe._require_every_row(summary, consumed=5, with_content=9)

    # And it must ALLOW a sheet that reconciles, or it refuses everything.
    arc_ampe._require_every_row(summary, consumed=7, with_content=9)


def test_the_extent_guard_is_actually_called(workbook, monkeypatch):
    """**A correct guard nobody calls is not a guard.**

    The test above proves the contract; it passes with the call site
    deleted. I found that by deleting each call in turn and recording
    which test noticed — two of four noticed nothing, and this pair is
    why. "The function is right" and "the function runs" are different
    claims and a unit test only makes the first.
    """
    called: list[tuple[int, int]] = []
    real = arc_ampe._require_every_row

    def spy(summary, *, consumed, with_content):
        called.append((consumed, with_content))
        return real(summary, consumed=consumed, with_content=with_content)

    monkeypatch.setattr(arc_ampe, "_require_every_row", spy)
    arc_ampe.parse_arc_ampe(workbook)

    assert called, "parse_arc_ampe returned without checking extent"


def test_scanning_for_the_header_does_not_extend_the_sheet(workbook):
    """openpyxl materializes rows read past a writable sheet's last one.

    Left unbounded, detection itself pushes `max_row` out to the scan depth
    and every later read walks rows this loader invented.
    """
    before = workbook["AE Mandatory Baseline"].max_row
    arc_ampe.find_controls_sheet(workbook)

    assert workbook["AE Mandatory Baseline"].max_row == before


# --------------------------------------------------------------------------
# The crosswalk
# --------------------------------------------------------------------------


def test_controls_are_anchored_only_on_ids_the_catalog_actually_defines(workbook):
    controls, summary = arc_ampe.parse_arc_ampe(workbook, nist_ids={"AC-1", "AC-2", "AC-2(1)"})
    by_id = {c.control_id: c for c in controls}

    assert by_id["AC-1"].source_crosswalk == {arc_ampe.NIST_SOURCE: "AC-1"}
    assert by_id["AC-3"].enhancements[0].source_crosswalk == {}
    assert summary.crosswalked == 3
    assert summary.unresolved == ["AC-3(8)"]


def test_without_a_catalog_nothing_is_crosswalked(workbook):
    """Usable on its own, just invisible to `policyforge map`."""
    controls, summary = arc_ampe.parse_arc_ampe(workbook)

    assert all(not c.source_crosswalk for c in controls)
    assert summary.crosswalked == 0
    assert summary.unresolved == []


# --------------------------------------------------------------------------
# The committed catalog
# --------------------------------------------------------------------------


@pytest.mark.skipif(not CATALOG.exists(), reason="ARC-AMPE catalog not built")
def test_the_committed_catalog_holds_the_baseline_cms_published():
    """402 is CMS's own published figure for an ACA Administering Entity.

    A count that drifts means the sheet moved, the header changed, or the
    decoy won — all of which produce a catalog that still parses.
    """
    controls = load_controls(CATALOG)
    items = len(controls) + sum(len(c.enhancements) for c in controls)

    assert items == 402
    assert all(c.baseline == arc_ampe.AE_BASELINE for c in controls)


def test_no_shipped_field_says_there_is_no_guidance():
    """**The artefact half, which the parser tests cannot cover.**

    `_guidance` running correctly says nothing about what was committed:
    `controls.json` is a static file, and a parser fixed after the
    catalog was generated leaves the boilerplate sitting in it. Nineteen
    fields shipped that way — nine control `discussion`s and ten
    enhancement `additional_requirements`.

    Stated over **every text field of every entry**, not over the one
    field the defect was found in. I found nine by looking at
    `discussion`; the fix found nineteen because it operates on the
    function rather than on the field I happened to check.
    """
    controls = load_controls(CATALOG)
    texts = [
        (c.control_id, field, getattr(c, field))
        for c in controls
        for field in ("control_statement", "discussion", "additional_requirements")
    ] + [
        (e.enhancement_id, field, getattr(e, field))
        for c in controls
        for e in c.enhancements
        for field in ("description", "additional_requirements")
    ]
    # Asked through the production decision rather than a copy of it, so the
    # guard cannot drift from the rule it is guarding — and so it asks the
    # same question: is this field *only* the boilerplate?
    boilerplate = [
        (ident, field) for ident, field, text in texts if text and arc_ampe._guidance(text) == ""
    ]

    assert boilerplate == []


# --------------------------------------------------------------------------
# Optional columns whose caption drifts (#265)
# --------------------------------------------------------------------------


def _drifted_book(replace: dict[str, str]):
    """The fixture's catalog sheet, with some header captions reworded."""
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("AE Mandatory Baseline")
    sheet.append([replace.get(caption, caption) for caption in HEADER])
    sheet.append(_row(1, "AC-01", "Policy", "a. Develop.", "•  Define roles.", "IA-1, PM-9"))
    sheet.append(_row(2, "AC-02(01)", "Automated", "Support.", "•  Use automation.", "AC-2"))
    return book


GUIDANCE_CAPTION = "ARC-AMPE SUPPLEMENTAL CONTROL REQUIREMENTS & GUIDANCE"


@pytest.mark.parametrize(
    ("replace", "missing"),
    [
        ({GUIDANCE_CAPTION: GUIDANCE_CAPTION.replace("&", "and")}, ["guidance"]),
        ({"Related Controls": "Related Control(s)"}, ["related"]),
        ({"Control Family": "Family"}, ["family"]),
        (
            {GUIDANCE_CAPTION: GUIDANCE_CAPTION.replace("&", "and"), "Related Controls": "Related"},
            ["guidance", "related"],
        ),
    ],
)
def test_a_drifted_optional_caption_still_parses_and_says_so(replace, missing):
    """**The parse succeeding is the defect's shape, not the fix's absence.**

    Each of these rewordings leaves the three required captions intact, so
    the sheet still qualifies and every control is read — with one column
    empty in every row. The count of controls is right; only the warning
    says anything is wrong, so the warning is what is asserted, first in the
    report and naming the field.
    """
    _, summary = arc_ampe.parse_arc_ampe(_drifted_book(replace))

    assert summary.controls + summary.enhancements == 2, "the sheet must still qualify"
    assert summary.missing_optional == missing
    report = summary.format_report()
    for index, field in enumerate(missing):
        assert report[index].startswith(f"WARNING: no '{field}' column")


def test_the_drift_really_empties_the_column():
    """The premise of the warning, measured rather than assumed: with `&`
    reworded, a row whose guidance cell is full reads as having none."""
    controls, summary = arc_ampe.parse_arc_ampe(
        _drifted_book({GUIDANCE_CAPTION: GUIDANCE_CAPTION.replace("&", "and")})
    )

    assert summary.with_guidance == 0
    assert controls[0].discussion == ""
    assert controls[1].enhancements[0].additional_requirements == ""  # AC-2(1), under AC-2


def test_the_shipped_captions_raise_no_warning(workbook):
    """The passing case. A warning that also fires on the real header is one
    everybody learns to read past, which is the state it exists to end."""
    _, summary = arc_ampe.parse_arc_ampe(workbook)

    assert summary.missing_optional == []
    assert not any(line.startswith("WARNING") for line in summary.format_report())


def test_the_related_count_is_reported(workbook):
    """It printed nothing before #265, so a column that read as empty in
    every row looked identical to one that was never parsed."""
    _, summary = arc_ampe.parse_arc_ampe(workbook)

    # AC-1 only. "None." lists nothing, and AC-2(1)'s "AC-2" belongs to an
    # enhancement, which has no related field to count.
    assert summary.with_related == 1
    assert f"{summary.with_related} controls list related controls." in summary.format_report()


def test_every_caption_is_either_required_or_warned_about():
    """**Derived, so a new caption cannot fall between the two.** A field
    added to `COLUMN_CAPTIONS` and to neither list would go silent in
    exactly the way #265 describes; the partition must cover the map."""
    fields = set(arc_ampe.COLUMN_CAPTIONS.values())

    assert set(arc_ampe.REQUIRED_FIELDS) | set(arc_ampe.OPTIONAL_FIELDS) == fields
    assert not set(arc_ampe.REQUIRED_FIELDS) & set(arc_ampe.OPTIONAL_FIELDS)
    assert set(arc_ampe.OPTIONAL_FIELDS) == {"family", "guidance", "related"}

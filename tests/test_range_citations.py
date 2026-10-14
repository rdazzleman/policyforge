"""A range or a comma list cites each member, when every member exists (#421).

80's ruling in the issue body: a SIMPLE range (endpoints identical except for
their last parenthesised part, a number, a lower-case letter or a lower-case
roman numeral; written `(1)-(3)` or `(b)(1)-(b)(3)`) resolves when every
member exists, and each member is cited. A missing member, or a cross-level
range, stays unresolved and reported. 80's 6058 adds the comma shorthand
`CP-9(1), (2), (3)`, by the same all-or-nothing rule.

Expected members are written out by hand, from the issue or the catalog.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.content.tags import is_citation_tag, is_unresolved_citation
from policyforge.ingest.schema import Control, ControlEnhancement, load_controls
from policyforge.topics.anchoring import member_readings
from policyforge.topics.satisfies import (
    catalog_scope,
    citation_members,
    document_evidence,
    parse_citations,
)

FRAMEWORKS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


def _hand_built(*ids: str) -> list[Control]:
    """A HIPAA-shaped catalog holding exactly `ids` under one section, plus
    another section of 40 ids, so no case here is decided by the cap on a
    run's length (a catalog of 4 ids refuses a 5-member run unread)."""
    enhancements = [ControlEnhancement(i, i, "", f"Text of {i}.") for i in ids]
    filler = [ControlEnhancement(f"164.399({k})", "", "", "x") for k in range(1, 41)]
    return [
        Control("164.316", "Policies", "HIPAA Security Rule", "2013", enhancements=enhancements),
        Control("164.399", "Filler", "HIPAA Security Rule", "2013", enhancements=filler),
    ]


def _members(tag: str, controls) -> list[str]:
    scope = catalog_scope(controls)
    return [i for _, i, _, _ in parse_citations(tag, scope.names, scope.index, scope)]


# ---- the issue's four cases, on a hand-built catalog ------------------------------


def test_a_range_against_a_catalog_holding_every_member_resolves_to_each():
    controls = _hand_built("164.316(b)(1)", "164.316(b)(2)", "164.316(b)(3)")
    assert _members("[HIPAA Security Rule 164.316(b)(1)-(3)]", controls) == [
        "164.316(b)(1)",
        "164.316(b)(2)",
        "164.316(b)(3)",
    ]
    # The long spelling of the same range.
    assert _members("[HIPAA Security Rule 164.316(b)(1)-(b)(3)]", controls) == [
        "164.316(b)(1)",
        "164.316(b)(2)",
        "164.316(b)(3)",
    ]


def test_a_roman_range_resolves():
    controls = _hand_built("164.316(b)(2)(i)", "164.316(b)(2)(ii)", "164.316(b)(2)(iii)")
    assert _members("[HIPAA Security Rule 164.316(b)(2)(i)-(iii)]", controls) == [
        "164.316(b)(2)(i)",
        "164.316(b)(2)(ii)",
        "164.316(b)(2)(iii)",
    ]


def test_a_range_missing_one_member_stays_unresolved_and_whole(run):
    """On the real catalog: `164.316(b)(3)` doesn't resolve cited alone, so
    the issue's own range is reported. (A flat hand-built catalog won't do:
    there every paragraph of 164.316 resolves alone, through its family.)"""
    assert _members("[HIPAA 164.316(b)(1)-(3)]", run.controls) == ["164.316(b)(1)-(3)"]
    assert is_unresolved_citation("[HIPAA 164.316(b)(1)-(3)]", run.scope)


def test_a_member_the_catalog_carries_through_a_child_resolves(run):
    """80's 6189 on PR 437: `164.316(b)(2)` is no id of the catalog, but it
    carries `(b)(2)(i)`-`(iii)` beneath it, so it is a member."""
    assert "164.316(b)(2)" not in run.scope.index["hipaa"]
    assert _members("[HIPAA 164.316(b)(1)-(2)]", run.controls) == [
        "164.316(b)(1)",
        "164.316(b)(2)",
    ]
    assert not is_unresolved_citation("[HIPAA 164.316(b)(1)-(2)]", run.scope)
    # A range of standards, each carried through its specifications (b5, 6190).
    assert _members("[HIPAA 164.308(a)(1)-(3)]", run.controls) == [
        "164.308(a)(1)",
        "164.308(a)(2)",
        "164.308(a)(3)",
    ]


def test_a_hand_built_member_through_a_child_resolves():
    controls = _hand_built("164.316(b)(1)", "164.316(b)(2)(i)")
    assert _members("[HIPAA Security Rule 164.316(b)(1)-(2)]", controls) == [
        "164.316(b)(1)",
        "164.316(b)(2)",
    ]


def test_a_range_invents_no_member_the_catalog_lacks():
    """80's 6189: the catalog carries neither `(iv)` nor `(v)`, so the range
    is reported whole. Since #438 neither resolves cited alone either: the
    catalog subdivides `164.316(b)(1)` into `(i)`-`(iii)`, and lists no more."""
    controls = _hand_built("164.316(b)(1)(i)", "164.316(b)(1)(ii)", "164.316(b)(1)(iii)")
    scope = catalog_scope(controls)
    assert not scope.cites("HIPAA Security Rule", "164.316(b)(1)(iv)")
    tag = "[HIPAA Security Rule 164.316(b)(1)(i)-(v)]"
    assert _members(tag, controls) == ["164.316(b)(1)(i)-(v)"]
    assert is_unresolved_citation(tag, scope)


def test_a_range_on_the_privacy_rule_invents_no_member(run_privacy):
    """Measured on PR 437 (6188): a range under a carried Privacy Rule id."""
    assert _members("[HIPAA 164.500(b)(1)(i)-(xx)]", run_privacy) == ["164.500(b)(1)(i)-(xx)"]
    assert _members("[HIPAA 164.500(b)(1)(i)-(v)]", run_privacy) == [
        f"164.500(b)(1)({r})" for r in ("i", "ii", "iii", "iv", "v")
    ]


@pytest.fixture(scope="module")
def run_privacy():
    return load_controls(FRAMEWORKS / "hipaa-privacy-rule" / "controls.json")


# ---- separators and trailing text (80's 6185 and 6187 on PR 437) -------------------

_EN_DASH = chr(0x2013)
_EM_DASH = chr(0x2014)


@pytest.mark.parametrize(
    ("tag", "members"),
    [
        ("[NIST CP-9(1) - (3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
        (f"[NIST CP-9(1) {_EN_DASH} (3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
        (f"[NIST CP-9(1){_EN_DASH}(3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
        ("[NIST CP-9(1) through (3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
        ("[NIST CP-9(1) to (3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
        ("[NIST CP-9(1) thru (3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
        (f"[NIST CP-9(1) {_EM_DASH} (3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
        (
            "[HIPAA 164.310(d)(2)(i) - (iv)]",
            [f"164.310(d)(2)({r})" for r in ("i", "ii", "iii", "iv")],
        ),
    ],
)
def test_a_spaced_or_worded_range_cites_every_member(tag, members, run):
    assert _members(tag, run.controls) == members
    assert not is_unresolved_citation(tag, run.scope)


@pytest.mark.parametrize(
    "tag",
    [
        "[NIST CP-9(1) see (3)]",
        "[NIST CP-9(1) - (99)]",
        "[NIST CP-9(1)-(3) or (5)]",
        "[NIST CP-9(1), (2) or (5)]",
    ],
)
def test_a_member_with_stray_text_is_reported_never_a_first_member(tag, run):
    """80's 6201 on PR 437: member-shaped parts not joined as a range or a
    list have the whole part reported; it never cites `CP-9(1)` alone. The
    last two reach `citation_members`' own check (b5's 6198)."""
    written = tag.strip("[]").removeprefix("NIST ")
    assert _members(tag, run.controls) == [written]
    assert is_unresolved_citation(tag, run.scope)


@pytest.mark.parametrize(
    ("tag", "cited"),
    [
        ("[NIST AC-2 - Account Management]", ("AC-2", "- Account Management")),
        (
            f"[HIPAA Security Rule 164.312(a)(1) {_EN_DASH} Access control]",
            ("164.312(a)(1)", f"{_EN_DASH} Access control"),
        ),
        ("[NIST CP-9(1) - see below]", ("CP-9(1)", "- see below")),
    ],
)
def test_a_dash_title_is_a_qualifier_and_resolves(tag, cited, run):
    """80's 6199 on PR 437 drops 6187's rule: these resolved at base."""
    scope = run.scope
    assert [(i, q) for _, i, q, _ in parse_citations(tag, scope.names, scope.index, scope)] == [
        cited
    ]
    assert not is_unresolved_citation(tag, scope)


@pytest.mark.parametrize(
    ("tag", "members"),
    [
        ("[NIST CP-9(1) and (3)]", ["CP-9(1)", "CP-9(3)"]),
        ("[NIST CP-9(1) & (2)]", ["CP-9(1)", "CP-9(2)"]),
        ("[NIST CP-9(1), (2), and (3)]", ["CP-9(1)", "CP-9(2)", "CP-9(3)"]),
    ],
)
def test_a_worded_list_cites_every_member(tag, members, run):
    """80's 6197 on PR 437: `and` and `&` join a list as a comma does."""
    assert _members(tag, run.controls) == members
    assert not is_unresolved_citation(tag, run.scope)


@pytest.mark.parametrize(
    "tag", ["[NIST CP-9(1) or (3)]", "[NIST CP-9(1) see (3)]", "[NIST CP-9(1) Moderate (3)]"]
)
def test_text_holding_another_member_is_reported_never_a_qualifier(tag, run):
    """80's 6197 on PR 437: whatever joins it, text holding a member-shaped
    part is no qualifier; with no range or list to make, it's reported."""
    written = tag.strip("[]").removeprefix("NIST ")
    assert _members(tag, run.controls) == [written]
    assert is_unresolved_citation(tag, run.scope)


def test_a_word_in_brackets_is_still_a_qualifier(run):
    scope = run.scope
    assert parse_citations("[NIST CP-9(1)-(3) (backups)]", scope.names, scope.index, scope) == [
        ("NIST", f"CP-9({k})", "(backups)", "") for k in (1, 2, 3)
    ]
    assert parse_citations("[NIST CP-9(1) (backups)]", scope.names, scope.index, scope) == [
        ("NIST", "CP-9(1)", "(backups)", "")
    ]


@pytest.mark.parametrize(
    ("whole", "spaced", "members", "qualifier"),
    [
        ("[NIST CP-9(1)-(3) Moderate]", "[NIST CP-9(1) - (3) Moderate]", (1, 2, 3), "Moderate"),
        ("[NIST CP-9(1)-(3) see below]", "[NIST CP-9(1) - (3) see below]", (1, 2, 3), "see below"),
        ("[NIST CP-9(1),(3) Moderate]", "[NIST CP-9(1) and (3) Moderate]", (1, 3), "Moderate"),
    ],
)
def test_spacing_never_changes_the_reading(whole, spaced, members, qualifier, run):
    """80's 6201 on PR 437: the same members and qualifier either way."""
    scope = run.scope
    expected = [("NIST", f"CP-9({k})", qualifier, "") for k in members]
    assert parse_citations(whole, scope.names, scope.index, scope) == expected
    assert parse_citations(spaced, scope.names, scope.index, scope) == expected


@pytest.mark.parametrize(
    ("tag", "cited"),
    [
        ("[HIPAA 164.312(a)(2)(iv) (A)]", [("164.312(a)(2)(iv)", "(A)")]),
        ("[HIPAA 164.308(a)(1)(ii)(A) (R)]", [("164.308(a)(1)(ii)(A)", "(R)")]),
        (
            "[HIPAA 164.308(a)(7)(ii)(A)-(C) (R)]",
            [(f"164.308(a)(7)(ii)({c})", "(R)") for c in "ABC"],
        ),
    ],
)
def test_hipaa_required_and_addressable_markers_are_qualifiers(tag, cited, run):
    """80's 6203 on PR 437 (1d's 6202): HHS writes Required and Addressable
    as a final `(R)` or `(A)`. A qualifier, kept as written, as at base."""
    scope = run.scope
    assert [(i, q) for _, i, q, _ in parse_citations(tag, scope.names, scope.index, scope)] == cited
    assert not is_unresolved_citation(tag, scope)


@pytest.mark.parametrize(
    "tag", ["[NIST CP-9(1) (A)]", "[HIPAA 164.312(a)(2)(iv) (B)]", "[NIST CP-9(1) (3)]"]
)
def test_any_other_lone_member_is_reported(tag, run):
    """80's 6203: the marker is HIPAA's, and only `(R)` and `(A)`."""
    written = tag.strip("[]").split(" ", 1)[1]
    assert _members(tag, run.controls) == [written]
    assert is_unresolved_citation(tag, run.scope)


def test_any_other_trailing_text_is_still_a_qualifier(run):
    scope = run.scope
    assert parse_citations("[NIST AC-2(3) Moderate, High]", scope.names, scope.index, scope) == [
        ("NIST", "AC-2(3)", "Moderate, High", "")
    ]
    # Written whole, a range keeps a qualifier after it, as a single id does.
    assert parse_citations("[NIST CP-9(1)-(3) Moderate]", scope.names, scope.index, scope) == [
        ("NIST", f"CP-9({k})", "Moderate", "") for k in (1, 2, 3)
    ]


def test_a_cross_level_range_stays_unresolved():
    controls = _hand_built("164.316(d)(1)", "164.316(d)(2)(i)", "164.316(d)(2)(iv)")
    assert _members("[HIPAA Security Rule 164.316(d)(1)-(d)(2)(iv)]", controls) == [
        "164.316(d)(1)-(d)(2)(iv)"
    ]


# ---- the reading itself -------------------------------------------------------------


def test_letters_and_an_ambiguous_run_read_as_the_catalog_has_them():
    """`(i)-(v)` is five roman numerals or fourteen letters; the catalog
    decides, all or nothing."""
    roman = _hand_built(*(f"164.316(a)({r})" for r in ("i", "ii", "iii", "iv", "v")))
    assert len(_members("[HIPAA Security Rule 164.316(a)(i)-(v)]", roman)) == 5
    letters = _hand_built(*(f"164.316({c})" for c in "abc"))
    assert _members("[HIPAA Security Rule 164.316(a)-(c)]", letters) == [
        "164.316(a)",
        "164.316(b)",
        "164.316(c)",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "164.316(b)(3)-(1)",
        "164.316(b)(1)-(iii)",
        "164.316(b)(1)",
        "164.316(b)(iiii)-(v)",
        "164.316(b)(1)-(c)(3)",
    ],
)
def test_no_reading_is_invented(text):
    """A backward run, mixed kinds, a single id, a malformed numeral, and
    endpoints under different parents (read as `(b)(1)`-`(b)(3)`, every
    member of which this catalog holds)."""
    controls = _hand_built("164.316(b)(1)", "164.316(b)(2)", "164.316(b)(3)")
    scope = catalog_scope(controls)
    assert citation_members("HIPAA Security Rule", text, scope) is None


@pytest.mark.parametrize(
    "text",
    ["SI-4(1)-(10000000)", "SI-4(1)-(" + "9" * 5000 + ")", "SI-4(i)-(" + "x" * 100000 + ")"],
    ids=["ten-million", "a-5000-digit-end", "a-100000-letter-numeral"],
)
def test_a_huge_range_is_refused_without_building_it(text, nist):
    """b5 on PR 437: `(1)-(10000000)` took 102 s and 1.2 GB to be refused.
    No catalog carries more members than it has ids."""
    started = time.perf_counter()
    assert citation_members("NIST", text, nist.scope) is None
    assert time.perf_counter() - started < 1


def test_a_run_is_built_up_to_its_limit_and_no_further():
    assert member_readings("X(1)-(3)", 3) == [["X(1)", "X(2)", "X(3)"]]
    assert member_readings("X(1)-(3)", 2) == []


@pytest.mark.parametrize("text", ["X(A)-(c)", "X(a)-(C)", "X(1)-(c)", "X(i)-(V)"])
def test_both_ends_are_one_kind(text):
    """80's 6192 on PR 437: number, lower-case letter, upper-case letter or
    lower-case roman numeral, the same at both ends."""
    assert member_readings(text, 1000) == []


def test_upper_case_is_letters_never_roman():
    assert member_readings("X(I)-(V)", 1000) == [[f"X({chr(c)})" for c in range(73, 87)]]


def test_an_upper_case_specification_range_resolves(run):
    """HIPAA's `(ii)(A)-(C)` (80's 6192 on PR 437)."""
    assert _members("[HIPAA 164.308(a)(7)(ii)(A)-(C)]", run.controls) == [
        "164.308(a)(7)(ii)(A)",
        "164.308(a)(7)(ii)(B)",
        "164.308(a)(7)(ii)(C)",
    ]


# ---- the comma shorthand (80, 6058) ---------------------------------------------------


@pytest.fixture(scope="module")
def nist():
    controls = load_controls(FRAMEWORKS / "nist-800-53-r5" / "controls.json")
    return SimpleNamespace(controls=controls, scope=catalog_scope(controls))


def test_a_comma_list_resolves_to_each_member(nist):
    assert _members("[NIST CP-9(1), (2), (3)]", nist.controls) == ["CP-9(1)", "CP-9(2)", "CP-9(3)"]
    assert _members("[NIST 800-53 SA-11(1)-(2), (8)-(9)]", nist.controls) == [
        "SA-11(1)",
        "SA-11(2)",
        "SA-11(8)",
        "SA-11(9)",
    ]


def test_a_comma_list_with_a_missing_member_is_reported_whole():
    enhancements = [ControlEnhancement(f"CP-9({k})", "", "", "x") for k in (1, 3)]
    controls = [Control("CP-9", "Backup", "NIST 800-53", "r5", enhancements=enhancements)]
    assert _members("[NIST CP-9(1), (2), (3)]", controls) == ["CP-9(1), (2), (3)"]


# ---- every reader, on the real HIPAA Security Rule --------------------------------------


@pytest.fixture(scope="module")
def run():
    controls = []
    for d in ("nist-800-53-r5", "arc-ampe", "hipaa-security-rule"):
        controls += load_controls(FRAMEWORKS / d / "controls.json")
    return SimpleNamespace(controls=controls, scope=catalog_scope(controls))


_RANGE = "[HIPAA 164.310(d)(2)(i)-(iv)]"


def test_satisfies_counts_each_member_and_reports_nothing(run):
    document = SimpleNamespace(
        relative_path="standards/media.md", title="Media", body=f"## M\n\nDispose. {_RANGE}\n"
    )
    evidence = document_evidence(document, controls=run.controls, crosswalk={})
    assert {c.key for c in evidence.cited} == {
        ("hipaa", "164.310(d)(2)(i)"),
        ("hipaa", "164.310(d)(2)(ii)"),
        ("hipaa", "164.310(d)(2)(iii)"),
        ("hipaa", "164.310(d)(2)(iv)"),
    }
    assert evidence.unknown == []


def test_generate_no_longer_lists_it_as_unresolved(run):
    assert is_citation_tag(_RANGE, run.scope)
    assert not is_unresolved_citation(_RANGE, run.scope)


def test_drift_reaches_a_document_citing_a_member_by_range(tmp_path):
    from policyforge.frameworks.drift import documents_citing

    root = tmp_path / "docs"
    (root / "standards").mkdir(parents=True)
    (root / "standards" / "m.md").write_text(f"# M\n\nDispose. {_RANGE}\n", encoding="utf-8")
    ids = {f"164.310(d)(2)({r})" for r in ("i", "ii", "iii", "iv")}
    hits = documents_citing(
        {"164.310(d)(2)(iii)"}, root, framework="HIPAA Security Rule", catalog_ids=ids
    )
    assert hits == {"164.310(d)(2)(iii)": ["standards/m.md"]}


def test_the_tag_form_keeps_the_range_and_its_block_lists_each_member(run):
    from policyforge.content.sources import add_source_blocks, normalize_tag, source_index

    assert normalize_tag(_RANGE, run.scope) == _RANGE
    out, added = add_source_blocks(
        f"## M\n\nDispose. {_RANGE}\n", source_index(run.controls), run.scope
    )
    assert added == 1
    for r in ("i", "ii", "iii", "iv"):
        assert f"**164.310(d)(2)({r})**" in out


def test_check_matches_a_range_in_the_synthesis_to_its_members_in_the_document(run):
    from policyforge.content.sources import citation_keys

    written = citation_keys(_RANGE, run.scope)
    spelled = set()
    for r in ("i", "ii", "iii", "iv"):
        spelled |= citation_keys(f"[HIPAA 164.310(d)(2)({r})]", run.scope)
    assert written == spelled

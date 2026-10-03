"""A heading that states an obligation is an obligation (#352).

80's ruling: heading text, ATX or setext, goes to the same two WARNING checks
as prose ("binds but cites nothing" and strength), with a message naming it
a heading. Before, both skipped heading text; #350 made that true of setext
headings too. None of the 33 generated Standards has such a heading (0 of
1,632 classify as binding), so these are hand-written, as ruled: 1d's table
on #352, every row now reported.
"""

from __future__ import annotations

import pytest

from policyforge.content.check import WARNING, check_tree

MUST = "Acme Health must retain all records for six years."
SHOULD = "Acme Health should retain records [NIST 800-53 AU-11]."
#: Binding and cited, so its section "cites" and nothing warns about it.
CITED_BELOW = "Accounts must be reviewed quarterly. [NIST 800-53 AC-2]"


def _findings(tmp_path, block: str) -> list[str]:
    """Warnings for a Standard: the block, then a cited sentence under it,
    so its section cites, as the uncited check requires."""
    body = f"# Standard\n\n{block}\n\n{CITED_BELOW}\n"
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "s.md").write_text(body, encoding="utf-8")
    report = check_tree(tmp_path, org_actors=("Acme Health",))
    return [
        f.message for f in report.findings if f.severity == WARNING and "owner" not in f.message
    ]


UNCITED = "cites nothing"
STRENGTH = "states a cited requirement"


@pytest.mark.parametrize(
    ("block", "expected", "is_heading"),
    [
        (f"{MUST}\n\n---", UNCITED, False),
        (f"{MUST}\n---", UNCITED, True),
        (f"The retention rule.\n{MUST}\n---", UNCITED, True),
        (f"## {MUST}", UNCITED, True),
        (f"{SHOULD}\n---", STRENGTH, True),
        (f"The retention rule.\n{SHOULD}\n---", STRENGTH, True),
        (f"## {SHOULD}", STRENGTH, True),
        (f"{MUST}\n===", UNCITED, True),
        (f"> ## {MUST}", UNCITED, True),
    ],
    ids=["must-blank-rule", "must-setext", "must-setext-2line", "must-atx", "should-setext",
         "should-setext-2line", "should-atx", "must-setext-equals", "must-quoted-atx"],
)  # fmt: skip
def test_every_row_of_the_table_is_reported(tmp_path, block, expected, is_heading):
    """1d's table on #352: each row was silent after #350 (or on the train,
    for ATX). Each is now a WARNING, and a heading is named as one."""
    warnings = [m for m in _findings(tmp_path, block) if expected in m]

    assert len(warnings) == 1, warnings
    assert ("heading" in warnings[0]) is is_heading, warnings[0]


@pytest.mark.parametrize(
    "block",
    [f"{MUST}\n---", f"The retention rule.\n{MUST}\n---", f"{MUST}\n==="],
    ids=["setext", "setext-2line", "setext-equals"],
)
def test_an_obligation_closing_a_citing_section_is_reported(tmp_path, block):
    """1d on #357, the realistic shape: the citation ABOVE, then the
    obligation made a heading by its underline, then the next section. That
    heading opens an empty section, so it is judged in the section it closes,
    which cites."""
    body = f"# Standard\n\n{CITED_BELOW}\n\n{block}\n\n## 2. Next\n\nNothing cited here.\n"
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "s.md").write_text(body, encoding="utf-8")
    messages = [f.message for f in check_tree(tmp_path).findings if UNCITED in f.message]
    assert len(messages) == 1 and "heading" in messages[0], messages


def test_a_heading_with_its_own_content_is_still_judged_in_its_own_section(tmp_path):
    """The other arm: a binding heading that DOES head content is judged
    there. Its own section cites nothing, so it stays silent even though
    the section before it cites."""
    body = f"# Standard\n\n{CITED_BELOW}\n\n## {MUST}\n\nNothing cited here.\n"
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "s.md").write_text(body, encoding="utf-8")
    assert [f.message for f in check_tree(tmp_path).findings if UNCITED in f.message] == []


def test_a_two_line_setext_heading_is_read_whole(tmp_path):
    """The obligation on the FIRST line of a two-line setext heading: the
    whole paragraph is the heading, so it is reported, not only its last line."""
    warnings = [
        m for m in _findings(tmp_path, f"{MUST}\nand what follows from it\n---") if UNCITED in m
    ]
    assert len(warnings) == 1 and "heading" in warnings[0], warnings


@pytest.mark.parametrize("underline", ["---", "==="])
def test_a_setext_heading_starts_a_section_for_the_uncited_check(tmp_path, underline):
    """The uncited rule is relative to the section, so where a section starts
    matters. A setext heading starts one, as an ATX heading does: the
    boilerplate below it sits in a section that cites nothing and stays
    silent. With ATX-only section starts, it would join the citing section
    above and warn."""
    body = (
        f"# Standard\n\n{CITED_BELOW}\n\nConformance\n{underline}\n\n"
        "Acme Health must follow this Standard.\n"
    )
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "s.md").write_text(body, encoding="utf-8")
    messages = [f.message for f in check_tree(tmp_path).findings if UNCITED in f.message]
    assert messages == []


@pytest.mark.parametrize(
    ("title", "warns"),
    [
        ("Password Must Be Rotated", True),
        ("Acme Health must retain all records for six years.", True),
        ("Personal Devices Are Prohibited", True),
        ("What Employees Must Do", False),
        ("What Managers Must Know", False),
        ("Must-Have Controls", False),
        ("Shall Statements", False),
        ("Shall and Should: How to Read This Standard", False),
        ("Controls That Must Be Applied", False),
        ("Required Training", False),
    ],
)
def test_80s_table_a_heading_warns_only_if_it_asserts(tmp_path, title, warns):
    """80's ruling on #357: subject + modal + verb, in any case, and passive
    counts. A title that only mentions a modal names a topic. Placed as 9b
    placed them: the heading's own section cites."""
    messages = [m for m in _findings(tmp_path, f"## {title}") if "heading" in m]
    assert bool(messages) is warns, messages


SECTION_SHAPES = {
    "bare-atx": "Plans must be kept. [NIST 800-53 PL-1]\n\n##\n\nAcme must act.\n",
    "bare-h1": "Plans must be kept. [NIST 800-53 PL-1]\n\n#\n\nAcme must act.\n",
    "atx-trailing-space": "Plans must be kept. [NIST 800-53 PL-1]\n\n## \n\nAcme must act.\n",
    "atx": "# Title\n\nText.\n\n## Scope\n\nMore.\n",
    "setext-two-line": "Intro.\n\nThe rule\nand more\n---\n\nBody.\n",
    "setext-equals": "Title\n=\n\nBody.\n\n## Next\n",
    "quoted-atx": "Intro.\n\n> ## Quoted\n\nBody.\n",
    "break-not-heading": "Intro.\n\n---\n\nBody.\n",
}


@pytest.mark.parametrize("shape", sorted(SECTION_SHAPES))
def test_section_starts_are_markdown_its_headings(shape):
    """**The oracle is markdown-it** (ba on #357), an independent parser: the
    lines the uncited check starts sections on are exactly the lines
    markdown-it opens a heading on. A bare `##` is a heading there."""
    from markdown_it import MarkdownIt

    from policyforge.content.grounding import _section_starts

    body = SECTION_SHAPES[shape]
    oracle = [
        t.map[0] + 1 for t in MarkdownIt("commonmark").parse(body) if t.type == "heading_open"
    ]
    assert _section_starts(body) == oracle


def test_an_obligation_under_a_bare_heading_is_in_its_own_section(tmp_path):
    """The case ba found: a bare `##` must start a section, so the uncited
    obligation below it is not judged in the citing section above."""
    body = f"# Standard\n\n{CITED_BELOW}\n\n##\n\nAcme Health must act on it.\n"
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "s.md").write_text(body, encoding="utf-8")
    assert [f.message for f in check_tree(tmp_path).findings if UNCITED in f.message] == []


def test_a_descriptive_heading_stays_silent(tmp_path):
    """`## Access Control` states nothing: no finding, as ruled."""
    assert _findings(tmp_path, "## Access Control") == []


def test_a_cited_title_is_not_a_weakened_requirement(tmp_path):
    """**A stated choice (b5), put to 80 on the PR:** a heading counts for
    the strength check only when it states something. A cited title such as
    `## Account Management [NIST 800-53 AC-2]` has no modality, and would
    otherwise be reported as "a statement of fact". The 33 generated
    Standards contain no cited heading, so this changes nothing measured."""
    assert _findings(tmp_path, "## Account Management [NIST 800-53 AC-2]") == []


def test_the_other_arm_a_heading_obligation_in_a_section_that_cites_nothing(tmp_path):
    """The uncited rule is relative to the section, for headings as for
    prose: a binding heading whose section cites nothing is boilerplate."""
    body = f"# Standard\n\n## {MUST}\n\nNothing here is cited.\n"
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "s.md").write_text(body, encoding="utf-8")
    messages = [f.message for f in check_tree(tmp_path).findings if UNCITED in f.message]
    assert messages == []


# --- conservation across the three heading gates (80's ruling, 1d's test) -----

SHAPES = {
    "atx": "## {text}",
    "atx-quoted": "> ## {text}",
    "setext-dashes": "{text}\n---",
    "setext-two-dashes": "{text}\n--",
    "setext-equals": "{text}\n===",
    "setext-two-line": "The rule.\n{text}\n---",
}
PAYLOADS = {
    "playbook": (
        "Acme Health must document it. [NIST AI RMF Playbook Govern 1.1 Action 1]",
        "Playbook",
    ),
    "uncited": (MUST, UNCITED),
    "strength": (SHOULD, STRENGTH),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("payload", sorted(PAYLOADS))
def test_every_heading_shape_reaches_every_heading_gate(tmp_path, shape, payload):
    """Every line `_line_kinds` calls heading text reaches all three gates:
    the Playbook heading rule (ERROR), the uncited check and the strength
    check (WARNING). Named exclusion: a heading stating nothing, for the
    strength check (see the cited-title test)."""
    from policyforge.content.deontic import _HEADING_TEXT, _line_kinds

    text, marker = PAYLOADS[payload]
    block = SHAPES[shape].format(text=text)
    lines = f"# Standard\n\n{block}\n".split("\n")
    kinds = _line_kinds(lines)
    assert any(k in _HEADING_TEXT and text in line for line, k in zip(lines, kinds, strict=True)), (
        "the premise: the payload is heading text in this shape"
    )

    body = f"# Standard\n\n{block}\n\n{CITED_BELOW}\n"
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "s.md").write_text(body, encoding="utf-8")
    messages = [f.message for f in check_tree(tmp_path, org_actors=("Acme Health",)).findings]

    assert any(marker in m for m in messages), (shape, payload, messages)

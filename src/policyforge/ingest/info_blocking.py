"""45 CFR Part 171 — information blocking — from eCFR's published XML.

**This part is not a control catalog in the sense 800-53 is**, and a reader
who takes it for one will misread every report it appears in. 800-53 says
what to implement. Part 171 defines a *practice* — information blocking —
and then sets out the exceptions: the conditions under which a practice
that would otherwise be blocking is not. So a control here is a condition
of an exception, and citing `171.203(a)` says a practice qualifies for the
security exception, not that a safeguard exists. The catalog's README says
this in the terms a buyer reads; it is repeated here because the next
person to change this parser will read the module before the README.

**The shape, and why.** A section is a control and its top-level lettered
paragraphs are its conditions, carried as enhancements; deeper nesting
stays in the condition's text. That is the same split `hipaa_loader` makes
between a Standard and its implementation specifications, so a reader who
knows one catalog can read the other. It is also what the regulation
contains: `171.203` is "Security exception" and (a)-(e) are the conditions
a practice must meet, while (d)(1)-(4) qualify condition (d) — the
contents of a written security policy — rather than standing as separate
duties.

**What is deliberately dropped.** `[Reserved]` sections — `171.402` today,
and Subparts E-I wholesale — carry a number and a title and no
obligations. A parse that emitted them would produce controls that are
countable, plausible and empty, which is the failure this project keeps
finding rather than a hypothetical one: every check except "is this
control real" passes on them. Definitions sections are dropped for the
reason `hipaa_loader` drops them, that a defined term is not a requirement.

Fetching is `ingest/ecfr.py`, shared with every regulation this project
reads. The wrappers below name 45 CFR 171's own coordinates, because
"which title and part is information blocking" is a property of the
regulation rather than a choice an ETL command should make.
"""

from __future__ import annotations

import re

# Parsed with the stdlib parser rather than regex over the raw markup, which
# is what `hipaa_loader` does for the neighbouring regulation. The difference
# is `<I>`: Part 171 names most of its conditions in an italic run inside the
# paragraph, and those names are content this catalog keeps. Matching `<P>`
# bodies textually would have to reproduce the element's own nesting rules to
# get them out.
#
# Both bandit (B405/B314) and semgrep (use-defused-xml) want `defusedxml`
# here. Their shared headline — XXE leaking confidential data — was measured
# against this parser rather than argued about, on CPython 3.12:
#
#   XXE local file read   REFUSED  ParseError: undefined entity &x;
#   external DTD fetch    ignored, nothing requested
#   billion laughs        EXPANDED 10,000 chars from four levels
#
# So the disclosure risk the rules describe does not reach this code: the
# stdlib parser refuses undefined entities outright and fetches no external
# DTD. The third line is the real one, and it is why this comment exists
# rather than a flat "false positive" — internal entity expansion works, and
# the classic nine-level payload reaches gigabytes.
#
# That bound is accepted deliberately. The input is one document fetched over
# HTTPS from ecfr.gov by an `etl-*` command the operator runs, and the worst a
# hostile response achieves is exhausting memory on the machine of the person
# who invoked it — a crash, not code execution and not data disclosure.
# `defusedxml` would convert that crash into a clean error, and costs a
# runtime dependency for every install plus a change to the Homebrew formula.
#
# **The premise this rests on: the host is fixed.** `ecfr._API` is the
# constant `https://www.ecfr.gov/api/versioner/v1`, with no override — so the
# XML cannot come from anywhere an attacker chooses. If the fetch ever accepts
# an operator-supplied URL, or this parser is pointed at third-party or
# user-supplied XML, every line above stops being true and `defusedxml`
# becomes the right answer. Stated because a change that parameterises the
# URL would invalidate this reasoning silently, without ever meeting it.
#
# ElementTree rather than regex over the markup — which is what `hipaa_loader`
# does for the neighbouring regulation — because of `<I>`: Part 171 names most
# of its conditions in an italic run inside the paragraph, and those names are
# content this catalog keeps. Matching `<P>` bodies textually would have to
# reproduce the element's own nesting rules to get them out.
#
# nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
import xml.etree.ElementTree as ET  # nosec B405

from policyforge.ingest import ecfr
from policyforge.ingest.schema import Control, ControlEnhancement

#: Part 171's own coordinates.
TITLE = 45
PART = "171"

#: **The declared name must be citable, which means it must not begin with
#: a digit.** `content/tags.SOURCE_TAG_RE` builds a framework name from
#: capital-initial words, so `[45 CFR 171 171.203(a)]` is not a tag at all -- it is
#: prose, and a document citing this catalog reported nothing cited while
#: `satisfies --strict` exited 0. `FRAMEWORK_ALIASES` cannot rescue it: the
#: pattern rejects the string before any normalisation runs, so the alias
#: table is downstream of the failure. The alias below pins the key; this
#: name is what makes the citation reachable at all.
FRAMEWORK = "Information Blocking"
FRAMEWORK_VERSION = "45 CFR Part 171"

#: A section identifier as this part writes them. Four digits are real and
#: load-bearing: Subpart J (disincentives) is 171.1000-171.1002 and Subpart
#: K (transparency) is 171.1100-171.1101. A pattern fitted to the first few
#: sections would drop five of twenty-four without saying so.
SECTION_ID_RE = re.compile(r"^171\.\d{3,4}$")

#: A condition identifier: a section, then one lettered paragraph.
CONDITION_ID_RE = re.compile(r"^171\.\d{3,4}\([a-z]\)$")

#: `(a)` opening a paragraph — the lettered conditions that are the
#: assessable units. Digits and roman numerals are deeper nesting and stay
#: inside the condition they qualify.
_LETTERED_RE = re.compile(r"^\(([a-z])\)\s*(.*)$", re.DOTALL)

#: `(1)` opening a paragraph — one level below a lettered condition.
_DIGIT_RE = re.compile(r"^\(\d+\)")

#: Single letters that are also roman numerals. `(i)` is both the ninth
#: letter and roman one, and eCFR's XML gives no structural hint: `(a)`,
#: `(1)` and `(i)` are flat sibling `<P>` elements at every depth. Today
#: every `(i)` in this part is roman — `171.303` has conditions (a), (b),
#: (c), and the five `(i)`s under its digits are numerals — so a regex
#: matching any single letter read that section as nine conditions, five
#: of them sharing the id `171.303(i)`. Excluding these letters outright
#: would be wrong the day a section reaches a ninth condition, hence the
#: successor rule in `_opens_condition`.
#:
#: Only `i`, `v` and `x` belong here. The wider roman alphabet does not:
#: `c`, `d`, `l` and `m` are 100 and up, which no paragraph list reaches,
#: while they are ordinary condition letters — including them cost
#: `171.201` its conditions (d), (e) and (f), because `(d)` arriving after
#: a digit was read as a numeral.
_ROMAN_LETTERS = frozenset("ivx")

_RESERVED_RE = re.compile(r"\[reserved\]", re.IGNORECASE)


def _text(node: ET.Element) -> str:
    """All text under `node`, whitespace collapsed.

    eCFR marks emphasis with `<I>` and `<E>` inline, so reading `.text`
    alone silently truncates a paragraph at its first italic word — which
    in this part is most of them, since the exceptions italicize their
    defined terms.
    """
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _section_number(head: str) -> str:
    """`§ 171.203 Security exception…` -> `171.203`."""
    match = re.search(r"(171\.\d{3,4})", head)
    return match.group(1) if match else ""


def _section_title(head: str, number: str) -> str:
    """The title as the regulation writes it, minus the section mark and number.

    Kept whole, including the question form the exceptions use ("Security
    exception—When will an actor's practice…"), because paraphrasing a
    regulation's own heading is how a catalog starts saying something the
    source does not.
    """
    title = head.split(number, 1)[-1].strip()
    return title.rstrip(".").strip()


#: A paragraph marker standing alone before an italic run — `(a) `, `(1) `.
_MARKER_ONLY_RE = re.compile(r"\s*\([a-z0-9]{1,4}\)\s*")


def _leading_heading(paragraph: ET.Element) -> str:
    """The regulation's own name for a condition, or `""`.

    Part 171 titles most of its conditions in italics immediately after the
    marker — `(a) <I>Reasonable belief.</I> The actor must…` — and those
    names are content, not styling: "Practice breadth" and "Type of harm"
    are how the preamble and the case law refer to these conditions. Fifty
    four of the part's paragraphs carry one.

    Only an italic run that *opens* the paragraph counts. The other
    thirty-nine italics in this part are defined terms sitting mid-sentence
    (and, in the definitions section, the terms themselves), which are not
    headings for anything.
    """
    children = list(paragraph)
    if not children or children[0].tag != "I":
        return ""
    if not _MARKER_ONLY_RE.fullmatch(paragraph.text or ""):
        return ""
    return re.sub(r"\s+", " ", "".join(children[0].itertext())).strip()


def _opens_condition(letter: str, previous: str | None, saw_digit: bool) -> bool:
    """Is this single-letter marker a new lettered condition, or a roman numeral?

    Lettered conditions run in order from `(a)`, so a marker is a condition
    only if it is the successor of the last one. `(i)` following `(c)` and
    a digit is roman one opening a deeper list; `(i)` following `(h)` is
    the ninth condition. The `saw_digit` guard resolves the case both
    readings fit — `(h)`, `(1)`, `(2)`, `(i)` — in favour of roman, because
    a list that has descended to digits continues downward rather than
    stepping back up to a sibling of `(h)`.
    """
    expected = "a" if previous is None else chr(ord(previous) + 1)
    if letter != expected:
        return False
    return not (saw_digit and letter in _ROMAN_LETTERS)


def parse_information_blocking(xml_text: str) -> list[Control]:
    """Parse eCFR's XML for 45 CFR Part 171 into Controls.

    One Control per section that carries obligations, each with its
    lettered conditions as enhancements. Reserved and definitions sections
    are dropped; see the module docstring for why each.

    **Every section the document contains is accounted for**, either as a
    control or as a named exclusion — see `_require_every_section`.
    """
    root = ET.fromstring(xml_text)  # nosec B314 — see the import.
    controls: list[Control] = []
    unaccounted: list[str] = []

    for section in root.iter("DIV8"):
        head = _text(section.find("HEAD")) if section.find("HEAD") is not None else ""
        number = _section_number(head) or (section.get("N") or "")
        if not SECTION_ID_RE.match(number):
            unaccounted.append(number or "<no number>")
            continue
        if _RESERVED_RE.search(head):
            continue
        title = _section_title(head, number)
        if title.lower().startswith("definitions"):
            continue

        control = Control(
            control_id=number,
            title=title,
            framework=FRAMEWORK,
            framework_version=FRAMEWORK_VERSION,
            source_path=f"https://www.ecfr.gov/current/title-45/section-{number}",
        )

        current: ControlEnhancement | None = None
        previous_letter: str | None = None
        saw_digit = False
        for paragraph in section.iter("P"):
            text = _text(paragraph)
            if not text:
                continue
            lettered = _LETTERED_RE.match(text)
            if lettered and _opens_condition(lettered.group(1), previous_letter, saw_digit):
                letter, body = lettered.group(1), lettered.group(2).strip()
                previous_letter = letter
                saw_digit = False
                heading = _leading_heading(paragraph)
                if heading and body.startswith(heading):
                    body = body[len(heading) :].strip()
                current = ControlEnhancement(
                    enhancement_id=f"{number}({letter})",
                    title=heading.rstrip("."),
                    baseline="",
                    description=body,
                )
                control.enhancements.append(current)
                continue
            if _DIGIT_RE.match(text):
                saw_digit = True
            # Deeper nesting — (1), (i) — and continuation prose. It belongs
            # to the condition it qualifies; before any condition opens it is
            # the section's own framing, which is the control statement.
            if current is not None:
                current.description = f"{current.description} {text}".strip()
            else:
                control.control_statement = f"{control.control_statement} {text}".strip()

        controls.append(control)

    _drop_reserved_enhancements(controls)
    _require_every_section(unaccounted)

    return controls


def _require_every_section(unaccounted: list[str]) -> None:
    """Refuse a parse that silently skipped a section the document has.

    **This asks the document, not a constant.** `unaccounted` holds every
    section-level node eCFR emitted that `SECTION_ID_RE` did not
    recognise, so the comparison tests the pattern against the source
    rather than against a number someone typed. A hand-written expected
    count is correct on the day it is written and unverifiable
    afterwards; both sides of this one come from the document being
    parsed, so it cannot go stale.

    `part2_loader._require_sections` is the same check with a different
    anchor, and its docstring carries the longer argument.

    **The failure it guards does not raise on its own.** A pattern that
    stops matching — eCFR renumbers, a subpart gains a prefix — yields a
    *smaller* catalog, not an error, and a smaller catalog passes every
    check that asks whether its entries are well-formed. Every count the
    README states would still be internally consistent. That is the shape
    that cost this project twelve fabricated ONC criteria, one catalog
    over.

    Reserved and definitions sections are **not** unaccounted: they are
    recognised and then excluded on purpose, which is the distinction
    between a decision and an omission.
    """
    if unaccounted:
        raise ValueError(
            f"{len(unaccounted)} section-level node(s) in the document were not "
            f"recognised as sections: {unaccounted}. Either eCFR changed how it "
            f"numbers Part 171, or SECTION_ID_RE no longer matches it — and the "
            f"catalog that would have been returned is smaller than the part."
        )


def _drop_reserved_enhancements(controls: list[Control]) -> None:
    """Drop conditions whose whole body is `[Reserved]`.

    The module docstring already says reserved text is dropped, but the
    check above it reads `<HEAD>` and so only ever sees a reserved
    *section* — `171.402`. A reserved *paragraph* inside a live section
    is a different shape and slipped through: `171.1001(b)` is the only
    one in the part today, and it shipped as a condition whose entire
    obligation is the word `[Reserved]`.

    That is the failure the docstring names, one level down — countable,
    plausible and empty, passing every check except "is this real".

    **Applied after the section is parsed, not while parsing it** — as a
    precaution, and said plainly because the distinction is not currently
    observable. Deeper paragraphs and continuation prose append to
    whichever condition is open, so declining to *create* a reserved one
    would redirect whatever follows it onto the previous condition
    instead of dropping it. `hipaa_loader._drop_empty_enhancements` is
    ordered this way, and learned it from exactly that.

    Part 171 cannot show it today: `171.1001(b)` is the last paragraph in
    its section, so nothing follows it to be misplaced. Implementing this
    the other way passes the whole suite. The ordering is kept because the
    next reserved paragraph need not be last, and the failure it would
    cause is a silent wrong-text one that no count check can see.
    """
    for control in controls:
        control.enhancements = [
            e for e in control.enhancements if not _RESERVED_RE.fullmatch(e.description.strip())
        ]


def current_ecfr_date() -> str:
    """The effective date eCFR publishes Title 45 as up to date to."""
    return ecfr.current_date(TITLE)


def ecfr_source_url(date: str) -> str:
    """The exact URL a given effective date is read from."""
    return ecfr.source_url(date, title=TITLE, part=PART)


def fetch_part_xml(*, date: str | None = None) -> str:
    """Fetch Part 171's full XML for the current or a given effective date."""
    return ecfr.fetch_part_xml(title=TITLE, part=PART, date=date)

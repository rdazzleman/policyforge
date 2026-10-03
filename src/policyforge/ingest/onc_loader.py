"""ONC certification criteria (45 CFR 170.315) as a control catalog.

**This catalog was merged once (#152) and reverted (#163) with categories
(b) and (e) substantially fabricated.** § 170.315 is one section of some
540 flat `<P>` elements whose hierarchy exists only in the text, and four
local rules -- successor-only, successor plus italic heading, the
regulation's self-citations, naive depth tracking -- each got it wrong,
because each was checked against itself. Read #163's analysis and #179
before changing anything here.

**What this parser does differently: it reads the markers' MARKUP, not only
their text.** eCFR italicises the markers of the two deepest levels --
`(<I>1</I>)`, `(<I>i</I>)` -- and leaves levels one to four plain. That is
what tells a criterion `(2)` from the `(<I>2</I>)` of a list three levels
inside the one before it, which is exactly the confusion that fabricated
`(b)(2)` through `(b)(13)` in #152: `itertext()` threw the signal away. A
citation-path tracker then places every marker as the successor at its
level, the first child one level down, or a repeat of the current value (a
second dated version of the same paragraph; eCFR carries two
`(b)(2)(iv)`s). A marker that fits none of those is an ERROR, never a guess.

**And its output is refused unless it equals an independent set.** The live
criterion ids must EQUAL `AGREED_CRITERIA`, which `policyforge-f8` derived
on #179 from two sources that never read this section's paragraph text
(ONC's test-method index, and the Federal Register's amendment instructions
applied to CHPL's list of every criterion ever used) and which agree. The
set is only ever compared against AFTER parsing -- never consulted during
it -- so the comparison is between two instruments, not one.
"""

from __future__ import annotations

import datetime as dt
import re

# The stdlib parser, knowingly, for the reason `info_blocking.py` measured
# and records at length: it refuses undefined entities and fetches no
# external DTD, and the one live risk -- internal entity expansion -- can
# only exhaust the memory of the operator who ran the fetch. **That rests
# on the host being fixed** (`ecfr._API`, no override) and on nothing else
# reaching this parser. `etl-onc` therefore takes no saved-XML option,
# deliberately: #179's first draft had one, and it would have pointed this
# parser at user-supplied XML, where `defusedxml` is the right answer.
# nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass, field

from policyforge.ingest import ecfr
from policyforge.ingest.schema import Control

TITLE = 45
PART = "170"
CRITERIA_SECTION = "170.315"

#: The declared name must be a legal source tag, so not `45 CFR 170`: a name
#: beginning with a digit is not one, the defect two CFR catalogs shipped
#: with. Taken from the section's own heading.
FRAMEWORK = "ONC Certification Criteria"
FRAMEWORK_VERSION = "45 CFR 170.315"

#: The 59 live certification criteria, from `policyforge-f8`'s step 1 on
#: #179 (accessed 2026-09-24): ONC's Test Method index, and the Federal
#: Register's amendment instructions (Cures, HTI-1, HTI-2, HTI-4) applied to
#: CHPL's list of every criterion ever used, AGREE on this set, (b)(11)
#: included -- two instruments, neither of which reads § 170.315's paragraph
#: text. This parser's live ids must EQUAL it; a regulation change that moves
#: it is refused for a person to re-pin, not absorbed.
AGREED_CRITERIA: frozenset[str] = frozenset(
    f"{CRITERIA_SECTION}({category})({number})"
    for category, numbers in {
        "a": (1, 2, 3, 4, 5, 12, 14, 15),
        "b": (1, 2, 3, 4, 7, 8, 9, 10, 11),
        "c": (1, 2, 3, 4),
        "d": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13),
        "e": (1, 3),
        "f": (1, 2, 3, 4, 5, 6, 7),
        "g": (1, 2, 3, 4, 5, 6, 7, 9, 10, 31, 32, 33),
        "h": (1, 2),
        "j": (20, 21),
    }.items()
    for number in numbers
)

_ROMAN = [
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "xiii",
    "xiv",
    "xv",
    "xvi",
    "xvii",
    "xviii",
    "xix",
    "xx",
    "xxi",
    "xxii",
    "xxiii",
    "xxiv",
    "xxv",
    "xxvi",
    "xxvii",
    "xxviii",
    "xxix",
    "xxx",
]

#: One marker, plain or italic, possibly a range `(4)-(8)` or `(11)-(30)`.
_MARKER = re.compile(
    r"\((?P<it><I>)?(?P<v>\d+|[a-z]+|[A-Z])(?(it)</I>)\)"
    r"(?:\s*[-\u2013\u2014]\s*\((?P<it2><I>)?(?P<last>\d+|[a-z]+|[A-Z])(?(it2)</I>)\))?"
)
_HEADING = re.compile(r"\s*<I>(?P<h>.*?)</I>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_RESERVED = re.compile(r"\[Reserved\]\.?")
_EXPIRES = re.compile(r"expires on (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})")


class OncParseError(RuntimeError):
    """§ 170.315 parsed to something the regulation cannot be. Raised, never
    returned: a catalog with one fabricated criterion is worse than none
    (80, on #179), and a fabrication is well-formed, so only a refusal shows
    it."""


@dataclass
class _Marker:
    value: str
    italic: bool
    last: str | None
    heading: str
    start: int  # offset in the paragraph's markup, for the criterion's text


def _chain(body: str) -> tuple[list[_Marker], str]:
    """The markers that open a paragraph, and the text after the last one.

    A marker may be followed by an italic heading, then an em dash or a
    space (or a newline) before the next marker: `(a) <I>Clinical</I>—(1)
    <I>CPOE—medications.</I> (i) Enable ...`.
    """
    position, markers = 0, []
    while True:
        match = _MARKER.match(body, position)
        if not match:
            break
        heading = ""
        position = match.end()
        head = _HEADING.match(body, position)
        if head:
            heading = " ".join(_TAGS.sub("", head.group("h")).split())
            position = head.end()
        markers.append(
            _Marker(
                match.group("v"),
                bool(match.group("it")),
                match.group("last"),
                heading,
                match.start(),
            )
        )
        rest = body[position:].lstrip().lstrip("—").lstrip()
        if rest.startswith("(") and _MARKER.match(rest):
            position = len(body) - len(rest)
            continue
        break
    return markers, " ".join(_TAGS.sub("", body[position:]).split())


def _levels(value: str, italic: bool) -> list[int]:
    """Which levels a marker's TYPE allows. eCFR: (a) 1, (1) 2, (i) 3,
    (A) 4, italic (1) 5, italic (i) 6."""
    if value.isdigit():
        return [5] if italic else [2]
    if italic:
        return [6] if value in _ROMAN else []
    if value.isupper():
        return [4]
    levels = [1] if len(value) == 1 else []
    if value in _ROMAN:
        levels.append(3)
    return levels


_FIRST = {1: "a", 2: "1", 3: "i", 4: "A", 5: "1", 6: "i"}


def _successor(level: int, value: str) -> str:
    if level in (2, 5):
        return str(int(value) + 1)
    if level in (3, 6):
        index = _ROMAN.index(value) + 1
        return _ROMAN[index] if index < len(_ROMAN) else ""
    return chr(ord(value) + 1)


@dataclass
class _Criterion:
    category: str
    number: int
    heading: str
    reserved: bool
    text: list[str] = field(default_factory=list)
    expires: dt.date | None = None


def criteria_paragraphs(xml_text: str) -> list[str]:
    """Every paragraph of § 170.315 as MARKUP, in document order. Markup, not
    text: the italics on a marker are the signal this parser needs."""
    root = ET.fromstring(xml_text)  # nosec B314 - see the import
    for division in root.iter("DIV8"):
        if (division.get("N") or "").startswith(CRITERIA_SECTION):
            return [
                re.sub(r"^<P>|</P>\s*$", "", ET.tostring(p, encoding="unicode").strip())
                for p in division.findall("P")
            ]
    return []


def _track(paragraphs: list[str]) -> tuple[dict[str, str], dict[str, _Criterion]]:
    """(category letter -> name, criterion id -> what the text says of it)."""
    stack: list[str] = []
    categories: dict[str, str] = {}
    criteria: dict[str, _Criterion] = {}
    current: _Criterion | None = None
    for index, body in enumerate(paragraphs):
        markers, tail = _chain(body)
        if not markers:
            if current is not None:
                current.text.append(" ".join(_TAGS.sub("", body).split()))
            continue
        for position, marker in enumerate(markers):
            options = []
            for level in _levels(marker.value, marker.italic):
                depth = len(stack)
                if level <= depth and marker.value == _successor(level, stack[level - 1]):
                    options.append(level)
                if level == depth + 1 and marker.value == _FIRST[level]:
                    options.append(level)
                if level <= depth and stack[level - 1] == marker.value:
                    options.append(level)  # a second dated version of one paragraph
            if not options:
                raise OncParseError(
                    f"paragraph {index}: marker ({marker.value}) fits no level after "
                    f"{''.join(f'({v})' for v in stack)} -- the structure is not what this "
                    "parser knows, so it refuses rather than guess."
                )
            level = max(options)
            stack = stack[: level - 1] + [marker.last or marker.value]
            last_in_chain = position == len(markers) - 1
            if level == 1:
                current = None
                categories[stack[0]] = marker.heading.rstrip(".")
            elif level == 2:
                own = tail if last_in_chain else ""
                reserved = not marker.heading and _RESERVED.fullmatch(own) is not None
                for number in range(int(marker.value), int(marker.last or marker.value) + 1):
                    criteria[f"{CRITERIA_SECTION}({stack[0]})({number})"] = _Criterion(
                        stack[0], number, marker.heading, reserved
                    )
                current = criteria[f"{CRITERIA_SECTION}({stack[0]})({stack[1]})"]
                current.text.append(" ".join(_TAGS.sub("", body[marker.start :]).split()))
            elif current is not None and position == 0:
                current.text.append(" ".join(_TAGS.sub("", body).split()))
                # An expiry is a sub-paragraph headed so ("Expiration of
                # criterion.") that states the date; read from the text.
                if marker.heading.lower().startswith("expiration of criterion"):
                    found = _EXPIRES.search(tail)
                    if found:
                        current.expires = dt.datetime.strptime(
                            found.group("date"), "%B %d, %Y"
                        ).date()
    return categories, criteria


def _title(criterion: _Criterion) -> str:
    """The criterion's italic heading, or -- where the regulation gives none,
    as for `(b)(11) Decision support interventions—` -- the text before its
    em dash."""
    if criterion.heading:
        return criterion.heading.rstrip(".").strip()
    first = re.sub(r"^\(\d+\)\s*", "", criterion.text[0]) if criterion.text else ""
    return first.split("—", 1)[0].strip().rstrip(".")


def parse_onc_criteria(xml_text: str, *, as_of: dt.date) -> tuple[list[Control], dict]:
    """The live certification criteria of § 170.315, and what was left out.

    `as_of` is the eCFR date the XML is current to: a criterion whose own
    text says it expired on or before that date is left out, like a reserved
    one, and both are returned by id so nothing is dropped unseen.
    """
    paragraphs = criteria_paragraphs(xml_text)
    if not paragraphs:
        raise OncParseError(f"no § {CRITERIA_SECTION} found in the XML")
    categories, criteria = _track(paragraphs)

    reserved = sorted(cid for cid, c in criteria.items() if c.reserved)
    expired = sorted(
        cid for cid, c in criteria.items() if not c.reserved and c.expires and c.expires <= as_of
    )
    live = {cid: c for cid, c in criteria.items() if cid not in reserved and cid not in expired}

    semicolon = sorted(cid for cid, c in live.items() if _title(c).endswith(";"))
    if semicolon:
        # 9b's tell from #163: no criterion title ends in a semicolon, and the
        # fabricated ones did ("Vital Signs;") -- no ground truth needed.
        raise OncParseError(f"criterion titles ending in ';', a sub-item's shape: {semicolon}")
    untitled = sorted(cid for cid, c in live.items() if not _title(c))
    if untitled:
        raise OncParseError(f"criteria with no title: {untitled}")

    if set(live) != AGREED_CRITERIA:
        raise OncParseError(
            "the parsed criteria differ from the independently agreed set "
            f"(invented: {sorted(set(live) - AGREED_CRITERIA)}, "
            f"lost: {sorted(AGREED_CRITERIA - set(live))}). Either the parse is wrong "
            "or the regulation changed; either way a person decides, and nothing is "
            "written (#179)."
        )

    def order(cid: str) -> tuple[str, int]:
        c = live[cid]
        return c.category, c.number

    controls = [
        Control(
            control_id=cid,
            title=_title(live[cid]),
            framework=FRAMEWORK,
            framework_version=FRAMEWORK_VERSION,
            family=categories.get(live[cid].category, ""),
            family_abbr=f"({live[cid].category})",
            control_statement="\n".join(live[cid].text),
            source_path=f"https://www.ecfr.gov/current/title-45/section-{CRITERIA_SECTION}",
        )
        for cid in sorted(live, key=order)
    ]
    return controls, {"reserved": reserved, "expired": expired}


def current_ecfr_date() -> str:
    """eCFR's current published date for Title 45."""
    return ecfr.current_date(TITLE)


def ecfr_source_url(date: str) -> str:
    return ecfr.source_url(date, title=TITLE, part=PART)


def fetch_part_xml(*, date: str | None = None) -> str:
    """The XML of 45 CFR 170 as eCFR served it on `date`."""
    return ecfr.fetch_part_xml(title=TITLE, part=PART, date=date)

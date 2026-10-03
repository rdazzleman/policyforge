"""42 CFR Part 2 — confidentiality of substance use disorder patient records.

**Two of this part's thirty-eight sections are controls. That is the whole
catalog, and the thinness is the correct answer rather than a parse
failure** — which is why the README states the count, states the test, and
names what was rejected.

**Part 2 is mostly conduct, not controls**, and that is the same trap 45 CFR
171 set. 800-53 says what to implement. Most of Part 2 says when a
disclosure is permitted, what a consent must contain, and what a court must
find before it orders a record produced. Citing § 2.66 asserts that a court
*may* authorise a disclosure; it does not assert that a control exists. A
catalog that crosswalked these to 800-53 by resemblance would be wrong in
the direction that is hardest to see, because every entry would look
plausible.

**One section is genuinely about security, and it was verified against the
fetched text rather than remembered.** § 2.16, "Security for records and
notification of breaches", requires formal policies and procedures covering
transfer and removal of paper records, destruction including sanitizing
hard-copy media to non-retrievable, secure rooms and locked containers,
workstation access, and de-identification to 45 CFR 164.514(b) — then the
same list for electronic records — and imports 45 CFR part 160 and subpart
D of part 164 for breach notification.

**§ 2.16 is a hub, and the test that sorts its references from its peers
is the reason this catalog has two entries rather than one or four:**

    Does this section impose an obligation that would not exist without it?
    If yes, it is a control. If it only says *apply § 2.16 to this
    population*, it is a requirement of § 2.16.

Applied mechanically rather than by judgement — an obligation is
independent if the section is the only place in the part where the
machinery appears, which is a set difference against § 2.16's own text that
anyone can re-run:

    2.19   CONTROL     13 terms absent from § 2.16 — encryption at rest,
                       separated decryption tools, backup on separate media,
                       labelled sealed containers, climate control, a
                       one-year sanitization deadline, retention periods
    2.52   derivative  0 terms unique to it; 2 references to § 2.16
    2.53   derivative  0 terms unique to it; 2 references to § 2.16

§ 2.19 is the one to notice, and *how* it hides is worth keeping. Its
heading says "Disposition of records by discontinued programs", and its
(a) *General* paragraph really is derivative — § 2.16's policies applied at
shutdown. **(b) *Special procedure* is a different regime entirely, and
that is where the controls live.** A reader asking "is this derivative?"
gets a correct yes from the paragraph that answers it and never reaches the
one that does not. The general/special split is a property of how
regulations are written, so the test is applied to a section's **full
text** and never to a quoted clause.

**Nothing emitted is empty, at any level.** A `[Reserved]` paragraph inside
a control that is being emitted is omitted from its requirements and its
absence asserted by id. Part 2 has **no** reserved sections and three
reserved paragraphs — § 2.19(b)(1)(i)(B), § 2.63(b), § 2.68(b) — so the
rule written for sections has nothing to bite on here and the paragraph
case is the only case. It is also the worse one: an empty entry sitting
alone in a catalog is conspicuous, while one hiding among real requirements
inherits that control's credibility. § 2.68(b) was reserved by the 2024
rule itself, so these are deliberate current drafting rather than stale
debris.

**Fetching is `ingest/ecfr.py`**, shared with every regulation this project
reads, and it already covers this part: `tests/test_ecfr_fetch.py` pins
`(42, "2", "title-42.xml?part=2")`. The wrappers below name Part 2's own
coordinates, because "which title and part is the confidentiality rule" is
a property of the regulation rather than a choice an ETL command makes.
"""

from __future__ import annotations

import re

# The XXE and entity-expansion analysis for this parser is in
# `info_blocking.py`, measured against CPython rather than argued from the
# rule text, and it applies here unchanged because this module parses the
# same source through the same parser.
#
# Restating only the condition that ends it, because a module that inherited
# the conclusion without the premise would keep the conclusion after the
# premise stopped holding: it rests on `ecfr._API` being a fixed constant, so
# the XML cannot come from a host an attacker chooses. If the fetch ever
# takes an operator-supplied URL, or this parser is pointed at third-party
# XML, `defusedxml` becomes the right answer here and there.
#
# nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass

from policyforge.ingest import ecfr
from policyforge.ingest.schema import Control, ControlEnhancement

#: Part 2's own coordinates.
TITLE = 42
PART = "2"

#: **The declared name must be citable, which means it must not begin with
#: a digit.** `content/tags.SOURCE_TAG_RE` builds a framework name from
#: capital-initial words, so `[42 CFR Part 2 2.16(a)]` is not a tag at all -- it is
#: prose, and a document citing this catalog reported nothing cited while
#: `satisfies --strict` exited 0. `FRAMEWORK_ALIASES` cannot rescue it: the
#: pattern rejects the string before any normalisation runs, so the alias
#: table is downstream of the failure. The alias below pins the key; this
#: name is what makes the citation reachable at all.
FRAMEWORK = "Substance Use Disorder Records"
FRAMEWORK_VERSION = "42 CFR Part 2"

#: A section identifier as *this part* numbers them, derived from the part
#: rather than adapted from a neighbour. Part 2 runs `2.1` to `2.68`: one or
#: two digits after the dot, never three.
#:
#: Stated as a measurement because the near miss is silent. Part 171's
#: pattern is `^171\.\d{3,4}$`, and against Part 2's 38 real section numbers
#: it matches **zero** — so a catalog built on a copied pattern comes out
#: empty, with nothing raising and every downstream check passing on the
#: nothing. `_require_sections` exists so that failure is loud.
SECTION_ID_RE = re.compile(r"^2\.\d{1,2}$")

#: How many sections Part 2 had at the 2026-09-17 revision. **A pinned
#: observation, not an invariant** — the CFR gains and loses sections, and
#: the day this number is wrong is a day someone should read the part
#: rather than a day the parser is broken.
#:
#: It is the *weaker* of the two count checks and it is second on purpose.
#: A hand-written expected count is something nobody can re-derive, so
#: `_require_sections` asks the document how many sections it has before it
#: consults this number at all — see there. This one exists for the failure
#: the document cannot self-report: a truncated or wrong response, where
#: the XML is internally consistent and simply is not the whole part.
EXPECTED_SECTIONS = 38

#: Sections that carry a number and a title and no obligation. Dropped for
#: the reason `hipaa_loader` drops them — a defined term is not a
#: requirement — and *asserted absent* rather than listed as exceptions,
#: because an exception list has to be kept in step with the source while an
#: assertion of absence cannot drift.
_NON_OBLIGATION_RE = re.compile(
    r"\[reserved\]|^definitions|^statutory authority|^purpose and effect",
    re.IGNORECASE,
)

#: The sections that impose a security obligation of their own, by the test
#: in the module docstring. **Enumerated rather than detected**, and that is
#: the deliberate choice: a heuristic that decided this at parse time would
#: quietly add a control the day a section's wording drifted, and adding a
#: control to a compliance catalog is not something that should happen
#: without a person.
#:
#: Two guards hold it, and they cover different things:
#: `test_the_catalog_still_matches_the_test_that_produced_it` sweeps every
#: parsed section and fails if the qualifying set stops equalling this one
#: -- so a section here ceasing to qualify, or one not here starting to, is
#: caught. `_require_sections` fails when the part carries a different
#: number of sections than `EXPECTED_SECTIONS` records, which is what
#: catches a *new* section arriving and forces someone to decide whether it
#: belongs here.
#:
#: Neither is a claim that this list cannot go stale. A revision that
#: rewords a section without changing the section count, in a way the
#: machinery terms do not reach, would pass both. The terms are a search,
#: not a definition -- see the README on § 2.66, where the one term the
#: sweep finds is a judicial seal rather than a container.
CONTROL_SECTIONS = ("2.16", "2.19")

#: A paragraph marker opening a top-level requirement: `(a)`, `(b)`. Digits
#: and roman numerals are deeper nesting and stay inside the requirement
#: they qualify, which is the split `info_blocking` makes and `hipaa_loader`
#: makes before it.
_LETTERED_RE = re.compile(r"^\(([a-z])\)\s*(.*)$", re.DOTALL)

#: `(1)` opening a paragraph — one level below a lettered requirement.
_DIGIT_RE = re.compile(r"^\(\d+\)")

#: Single letters that are also roman numerals. eCFR's XML gives no
#: structural hint — `(a)`, `(1)` and `(i)` are flat sibling `<P>` elements
#: at every depth — so `(i)` is ambiguous between the ninth letter and roman
#: one. `info_blocking._opens_condition` carries the full analysis of why
#: only `i`, `v` and `x` belong here and why `c`, `d`, `l` and `m` must not.
#:
#: **Measured on this part rather than inherited**: reading any single
#: letter as a requirement gave § 2.16 a phantom `2.16(i)` that stole the
#: paper-records list out of `2.16(a)`, and gave § 2.19 **two requirements
#: both numbered `2.19(i)`** plus a `2.19(v)`. Duplicate ids in a compliance
#: catalog are the silent kind of wrong: every entry is well-formed, the
#: count looks plausible, and two different obligations answer to one
#: citation.
_ROMAN_LETTERS = frozenset("ivx")

#: A paragraph whose whole body is `[Reserved]`. Matched at any depth,
#: because in this part every reserved paragraph is deep: the one inside a
#: control is `2.19(b)(1)(i)(B)`, four levels down, where it would otherwise
#: be folded into a requirement's prose rather than appearing as an entry.
#: That is the form 80's ruling is about — an empty statement inheriting the
#: credibility of the real requirements around it.
_RESERVED_PARAGRAPH_RE = re.compile(r"^\([a-z0-9ivx]{1,4}\)\s*\[reserved\]\.?$", re.IGNORECASE)


@dataclass(frozen=True)
class Section:
    """One numbered section of the part, as published."""

    number: str
    title: str
    text: str

    @property
    def states_an_obligation(self) -> bool:
        """Whether this section carries duties, as opposed to vocabulary.

        A title-level test, and deliberately only that. Whether a section
        that *does* carry duties carries a **control** is the open shape
        question, and it is not answerable from the heading — § 2.19's
        heading says "Disposition of records by discontinued programs" and
        its body is media sanitization. Nothing here should be read as
        deciding it.
        """
        return not _NON_OBLIGATION_RE.search(self.title)


def _text(node: ET.Element) -> str:
    """All text under `node`, whitespace collapsed.

    `itertext` rather than `.text`, because eCFR marks defined terms and
    paragraph names in `<I>` runs inside the paragraph, and those runs are
    content. Reading `.text` alone silently truncates at the first one.
    """
    return " ".join(" ".join(node.itertext()).split())


def _section_title(head: str, number: str) -> str:
    """The heading with its section symbol and number stripped.

    eCFR writes the whole heading into one node: "§ 2.16 Security for
    records and notification of breaches." The number is already the id.
    """
    stripped = re.sub(rf"^\s*§*\s*{re.escape(number)}\s*", "", head.lstrip("§ "))
    return stripped.strip()


def _require_sections(found: list[Section], *, in_document: list[str]) -> None:
    """Refuse a parse that found less than the document contains.

    The failure guarded against here does not raise on its own: a pattern
    that matches nothing yields an empty catalog, and an empty catalog
    passes every check that asks whether its entries are well-formed.

    **The primary check asks the document, not a constant.** `in_document`
    is every section-level node eCFR emitted, counted without consulting
    `SECTION_ID_RE` — so comparing it against `found` tests the pattern
    against the source rather than against a number someone typed. A
    hand-written expected count is the same shape as a hand-written prefix
    list: correct on the day it is written and unverifiable afterwards.
    This route needs no such number and cannot go stale, because both sides
    come from the document being parsed.

    `EXPECTED_SECTIONS` is consulted afterwards and catches only what that
    cannot: a response that is internally consistent and is not the whole
    part.
    """
    missed = [n for n in in_document if n not in {s.number for s in found}]
    if missed:
        raise ValueError(
            f"SECTION_ID_RE ({SECTION_ID_RE.pattern!r}) matched "
            f"{len(found)} of the {len(in_document)} sections that 42 CFR Part "
            f"{PART} actually contains. This is a parser fault rather than a "
            f"change in the regulation — the document says these sections are "
            f"there and the pattern did not match them: {', '.join(missed[:12])}"
            + (f" (+{len(missed) - 12} more)" if len(missed) > 12 else "")
        )
    if len(found) != EXPECTED_SECTIONS:
        raise ValueError(
            f"Parsed {len(found)} sections from 42 CFR Part {PART}, expected "
            f"{EXPECTED_SECTIONS} as of the 2026-09-17 revision. Every section "
            f"the document carries was matched, so the pattern is fine and the "
            f"document is not the one this parser was pinned against: either "
            f"the part changed, or the response was truncated. Read the diff, "
            f"then update EXPECTED_SECTIONS in the commit that accounts for it. "
            f"Found: {', '.join(s.number for s in found)}"
        )


def sections(xml: str, *, strict: bool = True) -> list[Section]:
    """Every numbered section of the part, in the order published.

    `strict` off parses an excerpt — a test fixture holds six sections by
    design — and is not a way to skip the count check on a real document.
    """
    root = ET.fromstring(xml)  # nosec B314

    # Every section-level node, counted before the pattern is applied. This
    # is the document's own answer to "how many sections are there", and it
    # is what `_require_sections` holds the pattern to.
    divisions = [d for d in root.findall(".//DIV8") if (d.get("N") or "").strip()]

    found: list[Section] = []
    for div in divisions:
        number = (div.get("N") or "").strip()
        if not SECTION_ID_RE.match(number):
            continue
        head = div.find("HEAD")
        found.append(
            Section(
                number=number,
                title=_section_title(_text(head) if head is not None else "", number),
                text=_text(div),
            )
        )

    if strict:
        _require_sections(found, in_document=[(d.get("N") or "").strip() for d in divisions])
    return found


def _leading_heading(paragraph: ET.Element) -> str:
    """The italic run that names a paragraph, or "".

    eCFR names most paragraphs that have one in an `<I>`: "Requirements for
    formal policies and procedures", "Special procedure where retention
    period required by law". It is the requirement's title, and it is the
    reason this parses the element rather than matching `<P>` bodies.
    """
    italic = paragraph.find("I")
    return _text(italic).strip().rstrip(".") if italic is not None else ""


def _opens_requirement(letter: str, previous: str | None, saw_digit: bool) -> bool:
    """Whether `(letter)` opens a top-level requirement rather than a
    roman numeral in a nested list.

    Lettered paragraphs run in order from `(a)`, so a marker is a
    requirement only if it is the successor of the last one. `(i)` after
    `(b)` and a digit is roman one opening a deeper list; `(i)` after `(h)`
    would be the ninth requirement. `saw_digit` resolves the case both
    readings fit in favour of roman, because a list that has descended to
    digits continues downward rather than stepping back up.

    The rule is `info_blocking._opens_condition`'s, and it is stated here
    rather than imported because the two parts do not have to agree: this
    one is answerable from Part 2's own text, where both `(i)` markers and
    the `(v)` are roman and no section reaches a ninth requirement.
    """
    expected = "a" if previous is None else chr(ord(previous) + 1)
    if letter != expected:
        return False
    return not (saw_digit and letter in _ROMAN_LETTERS)


def _require_unique_ids(controls: list[Control]) -> None:
    """Refuse a catalog where two requirements answer to one citation.

    The failure this guards against is silent by construction: every entry
    is well-formed, the count is plausible, and only a reader who follows
    the citation finds that it names two different obligations. It is how
    the roman-numeral bug showed up here — § 2.19 emitted `2.19(i)` twice —
    so the guard is the one that would have caught it without a person
    reading the output.
    """
    for control in controls:
        seen: dict[str, int] = {}
        for requirement in control.enhancements:
            seen[requirement.enhancement_id] = seen.get(requirement.enhancement_id, 0) + 1
        duplicated = sorted(rid for rid, count in seen.items() if count > 1)
        if duplicated:
            raise ValueError(
                f"42 CFR § {control.control_id} emitted more than one "
                f"requirement under the same id: {', '.join(duplicated)}. Two "
                f"obligations answering to one citation is a catalog that "
                f"looks well-formed and cites wrongly — check _opens_requirement "
                f"against this section's paragraph markers."
            )


def _requirements(section: ET.Element, number: str) -> tuple[str, list[ControlEnhancement]]:
    """(statement, requirements) for one section.

    Top-level lettered paragraphs are the requirements; digits and roman
    numerals qualify the one they sit under and are folded into its text.
    That is `info_blocking`'s split and `hipaa_loader`'s before it, so a
    reader who knows one catalog can read this one.

    Prose before the first `(a)` is the section's own framing and becomes
    the control statement.
    """
    statement = ""
    requirements: list[ControlEnhancement] = []
    current: ControlEnhancement | None = None
    previous_letter: str | None = None
    saw_digit = False

    for paragraph in section.iter("P"):
        text = _text(paragraph)
        if not text:
            continue

        # Dropped at whatever depth it appears, before it can be folded into
        # the prose of the requirement above it. `2.19(b)(1)(i)(B)` is four
        # levels down, so it never reaches the branch that would emit it --
        # which is exactly why it needs catching here rather than there.
        if _RESERVED_PARAGRAPH_RE.match(text):
            continue

        lettered = _LETTERED_RE.match(text)
        if lettered and _opens_requirement(lettered.group(1), previous_letter, saw_digit):
            letter, body = lettered.group(1), lettered.group(2).strip()
            previous_letter = letter
            saw_digit = False
            heading = _leading_heading(paragraph)
            if heading and body.startswith(heading):
                body = body[len(heading) :].lstrip(" .")
            current = ControlEnhancement(
                enhancement_id=f"{number}({letter})",
                title=heading,
                baseline="",
                description=body,
            )
            requirements.append(current)
            continue

        if _DIGIT_RE.match(text):
            saw_digit = True

        if current is not None:
            current.description = f"{current.description} {text}".strip()
        else:
            statement = f"{statement} {text}".strip()

    return statement, requirements


def parse_part2(xml: str, *, strict: bool = True) -> list[Control]:
    """42 CFR Part 2's security controls, which are two of its 38 sections.

    Not every section that states an obligation — most of this part states
    conduct, and a conduct rule cited as a control asserts that a safeguard
    exists where the regulation only says a disclosure was lawful. See the
    module docstring for the test that sorts them and for what it rejected.
    """
    root = ET.fromstring(xml)  # nosec B314
    by_number = {(d.get("N") or "").strip(): d for d in root.findall(".//DIV8")}

    controls: list[Control] = []
    for number in CONTROL_SECTIONS:
        division = by_number.get(number)
        if division is None:
            if strict:
                raise ValueError(
                    f"42 CFR § {number} is a control in this catalog and the "
                    f"document does not contain it. Found: "
                    f"{', '.join(sorted(by_number)) or '(no sections)'}"
                )
            continue

        head = division.find("HEAD")
        statement, requirements = _requirements(division, number)
        controls.append(
            Control(
                control_id=number,
                title=_section_title(_text(head) if head is not None else "", number),
                framework=FRAMEWORK,
                framework_version=FRAMEWORK_VERSION,
                control_statement=statement,
                enhancements=requirements,
                source_path=f"https://www.ecfr.gov/current/title-42/section-{number}",
            )
        )

    _require_unique_ids(controls)
    return controls


def current_ecfr_date() -> str:
    """The effective date eCFR publishes Title 42 as up to date to."""
    return ecfr.current_date(TITLE)


def ecfr_source_url(date: str) -> str:
    """The exact URL a given effective date is read from."""
    return ecfr.source_url(date, title=TITLE, part=PART)


def fetch_part_xml(*, date: str | None = None) -> str:
    """Fetch Part 2's full XML for the current or a given effective date."""
    return ecfr.fetch_part_xml(title=TITLE, part=PART, date=date)

"""Parse the HIPAA Security Rule (45 CFR Part 164, Subpart C) into this
project's common Control/Element schema.

Unlike HITRUST/GovRAMP, the HIPAA Security Rule is a US federal regulation
— public domain, same basis as NIST 800-53/FedRAMP/ARC-AMPE — so it's safe
to bundle directly rather than treat as BYOC. Source of truth is the
Electronic Code of Federal Regulations (eCFR)'s versioner API
(https://www.ecfr.gov/developers/documentation/api/v1), not a hand-copied
transcription, so `data/frameworks/hipaa-security-rule/controls.json` can
always be regenerated and diffed against the actual current regulation
text via `policyforge etl-hipaa`.

Parsing approach: within each `<DIV8 TYPE="SECTION">` (one CFR section,
e.g. § 164.308), every `<P>` starts with zero or more leading citation
tokens like `(a)`, `(1)`, `(i)`, `(A)` establishing its position in the
paragraph hierarchy — but a lone token's *meaning* (new top-level letter
vs. next roman numeral vs. next capital letter) is ambiguous from the
token alone (e.g. is `(i)` a roman numeral continuing an existing numbered
standard, or — hypothetically — a fifth top-level letter?). This is
resolved with a small state machine (`_CitationTracker`) using two facts
that hold throughout this specific Subpart: digits and uppercase letters
are unambiguous types, and a lowercase token is only ever a roman numeral
once a digit has already been seen at the current nesting point — otherwise
it's a new top-level letter. Verified against every section in Subpart C
(164.302-164.318); see tests/test_hipaa_loader.py.

Rather than assume Required/Addressable implementation specifications
always sit at a fixed citation depth (they don't — § 164.308 tags them at
the `(A)`/capital-letter level, § 164.310 tags them at the `(i)`/roman
level), each named `<I>...</I>` paragraph is classified by content: one
containing "(Required)" or "(Addressable)" becomes a `ControlEnhancement`
on the most recently opened `Control`; any other named paragraph (a
"Standard: ..." heading, or an unlabeled-but-titled requirement like
"Business associate contracts and other arrangements") opens a new
`Control`. Paragraphs with no italicized lead-in (pure connective prose,
or a bare "Implementation specifications:" label) update the citation
path but produce neither.
"""

from __future__ import annotations

import re

from policyforge.ingest import ecfr

from .schema import Control, ControlEnhancement

_SECTION_RE = re.compile(
    r'<DIV8 N="(?P<id>164\.\d+)" TYPE="SECTION"[^>]*>'
    r"\s*<HEAD>(?P<head>.*?)</HEAD>(?P<body>.*?)</DIV8>",
    re.DOTALL,
)
_PARA_RE = re.compile(r"<P>(?P<content>.*?)</P>", re.DOTALL)
# Some paragraphs introduce a second, nested citation mid-text after an
# em-dash rather than at the start of a new <P> (e.g. "(2) <I>Implementation
# specifications (Required)</I>&#x2014;(i) <I>Business associate
# contracts.</I> ..."). Splitting on this decomposes one such paragraph into
# multiple virtual paragraphs so the rest of the logic below doesn't need to
# know about the distinction.
_COMPOUND_SPLIT_RE = re.compile(r"&#x2014;(?=\([a-zA-Z0-9]+\))")
_LEADING_TOKENS_RE = re.compile(r"^((?:\([a-zA-Z0-9]+\))+)\s*")
_TOKEN_RE = re.compile(r"\(([a-zA-Z0-9]+)\)")
_ITALIC_RE = re.compile(r"<I>(?P<italic>.*?)</I>\s*(?P<rest>.*)", re.DOTALL)
_REQUIRED_ADDRESSABLE_RE = re.compile(r"\((Required|Addressable)\)")
# Ambiguous single-letter tokens (e.g. "c", "d") could be roman numerals
# (C=100, D=500) or the next top-level paragraph letter. Restricting the
# "treat as roman" branch to plausible small list positions — rather than
# any string built from roman-numeral characters — resolves the ambiguity
# correctly in practice: a real roman-numeral list here only ever counts
# 1-10, while top-level letters routinely reach 'c'/'d'/'l'/'m'.
_PLAUSIBLE_ROMAN_RE = re.compile(r"^(i|ii|iii|iv|v|vi|vii|viii|ix|x)$")


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&#xA7;", "§").replace("&#x2014;", "—")
    text = text.replace("&#x201C;", "“").replace("&#x201D;", "”")
    text = text.replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


class _CitationTracker:
    """Tracks the current full citation path (e.g. ['a', '1', 'ii']) across
    a section's paragraphs. See module docstring for the disambiguation
    rule this depends on."""

    def __init__(self, section_id: str):
        self.section_id = section_id
        self._path: list[str] = []

    def _token_depth(self, token: str) -> int:
        """Where a single leading token belongs in the path, given the
        path built up so far *within this same leading-token group*."""
        if token.isdigit():
            return 2
        if token.isupper():
            return 4
        # Ambiguous lowercase: a roman numeral continuing an existing
        # numbered standard, or a new top-level letter.
        if self._path and len(self._path) >= 2 and _PLAUSIBLE_ROMAN_RE.match(token):
            return 3
        return 1

    def advance(self, leading_tokens: str) -> str:
        """Update the path from a paragraph's leading `(x)(y)...` group
        (may be empty) and return the resulting full citation string."""
        for token in _TOKEN_RE.findall(leading_tokens):
            depth = self._token_depth(token)
            self._path = self._path[: depth - 1] + [token]
        return self.section_id + "".join(f"({t})" for t in self._path)


def _parse_section(section_id: str, head: str, body: str) -> list[Control]:
    # Head is "§ 164.NNN Title." — three whitespace-separated tokens
    # (section mark, number, title), so split at most twice.
    title = _strip_tags(head).split(None, 2)[-1].rstrip(".")
    if title.lower() == "definitions":
        return []  # defined terms aren't requirements

    tracker = _CitationTracker(section_id)
    controls: list[Control] = []
    current: Control | None = None
    # The most recently opened item — either `current` itself or its most
    # recent enhancement, whichever is more specific. Un-italicized
    # continuation paragraphs (e.g. a bare "(A) Comply with ...;" list item
    # under an already-open requirement) get appended to whichever this is,
    # rather than silently dropped, since real requirement text lives there
    # too — CFR sections don't italicize every list item, only the first
    # one that introduces the list.
    current_enh: ControlEnhancement | None = None
    # Framing prose before any item opens, with the citation it sits at:
    # `(a) A covered entity ... must, in accordance with § 164.306:`.
    lead_ins: list[tuple[str, str]] = []

    for match in _PARA_RE.finditer(body):
        for raw in _COMPOUND_SPLIT_RE.split(match.group("content")):
            leading_match = _LEADING_TOKENS_RE.match(raw)
            leading = leading_match.group(1) if leading_match else ""
            remainder = raw[leading_match.end() :] if leading_match else raw
            citation = tracker.advance(leading)

            italic_match = _ITALIC_RE.match(remainder.strip())
            if not italic_match:
                text = _strip_tags(remainder)
                target = current_enh or current
                if text and target is not None:
                    field = (
                        "description"
                        if isinstance(target, ControlEnhancement)
                        else "control_statement"
                    )
                    setattr(target, field, f"{getattr(target, field)} {text}".strip())
                elif text:
                    lead_ins.append((citation, text))
                continue  # otherwise: framing prose before any item has opened
            italic_text = _strip_tags(italic_match.group("italic"))
            rest_text = _strip_tags(italic_match.group("rest"))

            if italic_text.rstrip(":") in (
                "Implementation specifications",
                "Implementation specification",
            ):
                continue  # bare section-header label, not itself a requirement

            req_match = _REQUIRED_ADDRESSABLE_RE.search(italic_text)
            if req_match:
                spec_title = italic_text[: req_match.start()].strip(" :.")
                if current is None:
                    continue  # malformed input — spec with no preceding standard
                current_enh = ControlEnhancement(
                    enhancement_id=citation,
                    title=spec_title,
                    baseline=req_match.group(1),
                    description=rest_text,
                )
                current.enhancements.append(current_enh)
                continue

            # New named item: a "Standard: ..." heading, or an unlabeled-but-
            # titled requirement (e.g. "Business associate contracts...").
            item_title = italic_text.strip(" :.")
            if item_title.lower().startswith("standard:"):
                item_title = item_title[len("standard:") :].strip()
            current = Control(
                control_id=citation,
                title=item_title,
                framework="HIPAA Security Rule",
                framework_version="45 CFR 164 Subpart C",
                control_statement=rest_text,
                source_path=f"https://www.ecfr.gov/current/title-45/section-{section_id}",
            )
            current_enh = None
            controls.append(current)

    _drop_empty_enhancements(controls)
    _relate_lead_ins(section_id, lead_ins, controls)
    return controls


#: A section cited in a lead-in: `§ 164.306`, or each of `§§ 164.306 and 164.308`.
_CITED_SECTION_RE = re.compile(r"\b(164\.\d+)\b")


def _relate_lead_ins(section_id: str, lead_ins: list[tuple[str, str]], controls) -> None:
    """Carry what a dropped lead-in says onto the controls it frames (#268).

    Four sections open with `A covered entity or business associate must, in
    accordance with § 164.306:` and then list their standards. The lead-in
    is dropped, deliberately: prepending it would rewrite 20 standards' text
    to record a relationship (80's ruling on #268). **The relationship is the
    source's**, so each control the lead-in frames names the cited section
    in `related_controls`, exactly as the source cites it (`164.306`), and
    nothing more.

    Framed means: the control's citation sits under the lead-in's. The one
    in § 164.308 is at `(a)`, so § 164.308(b) is not framed by it; the other
    three have no leading token and frame the whole section.

    **A lead-in that cites a section is never dropped silently.** If it
    frames no control, the parse refuses, because then the relationship
    would be lost with nothing saying so. A lead-in that cites nothing
    (§ 164.302's applicability sentence) has nothing to carry.
    """
    for citation, text in lead_ins:
        cited = list(dict.fromkeys(_CITED_SECTION_RE.findall(text)))
        if not cited and "§" not in text:
            continue  # cites nothing at all: nothing to carry
        # Keyed on what was FOUND, not on the `§` sign (1d on #355): a lead-in
        # naming `164.306` without `§` is carried, and one with `§` citing
        # nothing this loader reads is refused below rather than skipped.
        framed = [
            c
            for c in controls
            if c.control_id == citation or c.control_id.startswith(citation + "(")
        ]
        if not cited or not framed:
            raise ValueError(
                f"§ {section_id}: the lead-in at {citation} ({text[:80]!r}) cites "
                f"{cited or 'no section this loader can read'} and frames {len(framed)} "
                "control(s); the relationship would be dropped with nothing saying so."
            )
        for control in framed:
            control.related_controls.extend(s for s in cited if s not in control.related_controls)


def _drop_empty_enhancements(controls: list[Control]) -> None:
    """Remove implementation specifications that carry no obligation.

    **Nothing emitted is empty**, the rule this project applies one level up
    to `[Reserved]` sections and paragraphs. An entry with a title and no
    text counts, renders and crosswalks while saying nothing, and every
    check except "is this requirement real" passes on it.

    One entry in one catalog meets this today: `164.314(a)(2)`,
    *"Implementation specifications"*, in the Security Rule. Its source
    paragraph is a run-in heading that delegates everything to its
    children:

        (2) Implementation specifications (Required)-(i) Business associate
            contracts. The contract must provide that the business
            associate will-

    **No content is lost by dropping it.** All three children are emitted
    as controls in their own right -- `(a)(2)(i)`, `(a)(2)(ii)` and
    `(a)(2)(iii)` -- so the container's entire content is those entries.
    What it cost was worse than a blank row: a user citing `164.314(a)(2)`
    cited a title, and an assessor following the citation found nothing,
    while the obligation they wanted sat at `(a)(2)(i)`.

    The contrast that shows this is not the phrase's fault is in the same
    section. `164.314(b)(2)` has the **identical title** and a 771-
    character description, because there the text really does follow the
    label. So this drops containers that delegate, and keeps every
    specification that states something.

    Applied after parsing rather than at creation, and that ordering is
    load-bearing: prose following an italic run is appended to the open
    enhancement as the loop proceeds, so a specification's description is
    not final until the section is. Skipping creation instead would
    redirect that trailing prose onto the parent `Control`, which changes
    entries that were correct.

    Empty `control_statement` is deliberately **not** touched. A heading-
    shaped control legitimately has none -- `164.314(a)(1)` and `(b)(1)`
    carry statements because those sections state obligations, and the
    purely structural ones do not.
    """
    for control in controls:
        control.enhancements = [e for e in control.enhancements if e.description.strip()]


#: The Security Rule's own coordinates. Named here rather than passed by
#: every caller, because "which title and part is HIPAA" is a property of
#: this regulation and not a choice an ETL command should make.
TITLE = 45
PART = "164"


def current_ecfr_date() -> str:
    """The effective date eCFR publishes Title 45 as up to date to."""
    return ecfr.current_date(TITLE)


def ecfr_source_url(date: str) -> str:
    """The exact URL a given effective date is read from."""
    return ecfr.source_url(date, title=TITLE, part=PART)


def fetch_ecfr_subpart_c_xml(*, date: str | None = None) -> str:
    """Fetch the current (or a specific effective date's) full XML text of
    45 CFR Part 164 from eCFR's public versioner API.

    These three are thin wrappers over `ingest/ecfr.py`, which serves every
    regulation this project reads. They keep their names and signatures
    because the CLI and the tests use them, and because "the HIPAA loader
    fetches HIPAA" is the right thing for a reader of this module to see.
    """
    return ecfr.fetch_part_xml(title=TITLE, part=PART, date=date)


def parse_hipaa_security_rule(xml_text: str) -> list[Control]:
    """Parse eCFR's XML for 45 CFR Part 164 (or just its Subpart C excerpt)
    into a flat list of Controls, one per "Standard" (or per unlabeled-but-
    titled requirement), each carrying its Required/Addressable
    implementation specifications as ControlEnhancements.

    Only Subpart C (Security Standards) sections are parsed; anything from
    Subpart A/B/D/E present in a full-Part-164 fetch is ignored — this
    project targets the Security Rule specifically, not HIPAA Privacy or
    Breach Notification.
    """
    subpart_start = xml_text.find('<DIV6 N="C" TYPE="SUBPART"')
    if subpart_start == -1:
        raise ValueError("Subpart C not found in the given XML — check the eCFR fetch.")
    subpart_end = xml_text.find("<DIV6", subpart_start + 10)
    subpart_text = xml_text[subpart_start : subpart_end if subpart_end != -1 else None]

    controls: list[Control] = []
    recognised: list[str] = []
    for match in _SECTION_RE.finditer(subpart_text):
        recognised.append(match.group("id"))
        controls.extend(_parse_section(match.group("id"), match.group("head"), match.group("body")))

    _require_every_section(recognised, in_subpart=_ANY_SECTION_RE.findall(subpart_text))
    _require_related_sections_exist(controls)
    return controls


def _require_related_sections_exist(controls: list[Control]) -> None:
    """Every section named in `related_controls` yields controls here (#268),
    so `164.306` points at § 164.306(a)-(e) in this catalog, not at nothing."""
    ids = [c.control_id for c in controls]
    dangling = sorted(
        {
            ref
            for c in controls
            for ref in c.related_controls
            if not any(i == ref or i.startswith(ref + "(") for i in ids)
        }
    )
    if dangling:
        raise ValueError(f"related_controls name section(s) with no control here: {dangling}")


#: Every section-level node in the subpart, counted **without** consulting
#: `_SECTION_RE`'s `164\.\d+` identifier. The two are compared below, so
#: the check tests the pattern against the document rather than against a
#: number someone typed.
_ANY_SECTION_RE = re.compile(r'<DIV8 N="([^"]+)" TYPE="SECTION"')


def _require_every_section(recognised: list[str], *, in_subpart: list[str]) -> None:
    """Refuse a parse that skipped a section Subpart C contains.

    **The failure this guards does not raise on its own.** `_SECTION_RE`
    pins the identifier as `164\\.\\d+`; if eCFR renumbers, adds a suffix,
    or changes the attribute order, the pattern matches *fewer* sections
    and returns a smaller catalog. Not an error — a catalog that is
    internally consistent, well-formed, and short.

    Every other check in this module asks whether an entry is right. This
    is the only one that asks whether they are **all here**, and those are
    different questions. The ONC catalog shipped twelve fabricated
    criteria through a loader that asked only the first.

    Compared against the **document**, never a constant: `in_subpart` is
    counted with a pattern that does not know what a section identifier
    looks like, so both sides come from the XML being parsed and neither
    can go stale. `part2_loader._require_sections` and
    `info_blocking._require_every_section` are the same check with
    different anchors.
    """
    missed = [n for n in in_subpart if n not in recognised]
    if missed:
        raise ValueError(
            f"Subpart C contains {len(in_subpart)} section(s) and only "
            f"{len(recognised)} were recognised; missed {missed}. Either eCFR "
            f"changed how it numbers the Security Rule, or _SECTION_RE no longer "
            f"matches it — and the catalog returned would be short, not wrong."
        )

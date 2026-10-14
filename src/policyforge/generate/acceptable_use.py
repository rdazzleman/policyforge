"""The Acceptable Use Policy and its Privileged User Rules addendum (418;
80's spec in comment 5926, amended by 6334; research in 5923).

An AUP is the rules of behaviour every member of the workforce signs before
access. It draws on several topics, so it is not a topic's Policy: it is a
document kind of its own, over a **fixed control set** taken by id from the
800-53 catalog, whichever topics anchor those controls.

**What the model writes and what code writes.** The model drafts each
section's rules in plain language, from the text of that section's controls.
Code writes everything a reader relies on to find their way and to sign: the
title, every section's heading in a fixed order (a section the draft left
out gets a placeholder, and the run names it), the acknowledgment block PL-4
asks for, and the traceability appendix. The appendix names each section's
800-53 controls and whatever the run's crosswalk maps them to, so a HIPAA or
HITRUST requirement appears only when the organisation's own catalogs put it
there.

**No inline tags, no invented values.** The body carries no citation: a
section's controls are in the appendix. A value the organisation decides
(how much personal use, which storage and AI tools, how fast to report) stays
a placeholder in brackets; the model is told so, and nothing in code supplies
one.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from policyforge.llm.base import LLMProvider
from policyforge.llm.prompts import Prompt, register

AUP_TITLE = "Acceptable Use Policy"
ADDENDUM_TITLE = "Privileged User Rules"
ACKNOWLEDGMENT_HEADING = "Acknowledgment of Receipt"
APPENDIX_HEADING = "Traceability"

#: The section a draft left out: a placeholder, so `check` reports it.
MISSING_SECTION = "[{title}: rules to be written]"


@dataclass(frozen=True)
class Section:
    """One section of the document: its heading, the 800-53 controls it
    draws on, and one line on what it covers (5b's table in 5923)."""

    title: str
    controls: tuple[str, ...]
    covers: str


#: The Acceptable Use Policy's sections, in order: 5923's sections 1-12 and
#: 14-15 (5926). The AI-use section is left out only for an organisation that
#: declared `ai: none` (22 part 3; 80's 6334); `aup_sections` applies that.
AUP_SECTIONS: tuple[Section, ...] = (
    Section("Scope", ("PL-4",), "who these rules apply to: staff, contractors and third parties"),
    Section(
        "Acknowledgment",
        ("PL-4", "PS-6"),
        "a documented acknowledgment before access, again on a schedule, and when the rules change",
    ),
    Section("Personal Use", ("PL-4",), "whether, and how far, personal use of systems is allowed"),
    Section(
        "Prohibited Activities",
        ("CM-10", "CM-11"),
        "what nobody may do with the organization's systems, software and data",
    ),
    Section(
        "Monitoring Notice",
        ("AC-8",),
        "that use of the systems may be monitored and recorded, and what consent that implies",
    ),
    Section(
        "Credentials",
        ("AC-6", "PL-4"),
        "keeping accounts and authenticators to oneself, and using only the access granted",
    ),
    Section(
        "Data Handling",
        ("PT-2",),
        "handling personal and sensitive information only for its authorized purpose",
    ),
    Section(
        "Devices",
        ("AC-19", "AC-20", "MP-7"),
        "organization and personal devices, external systems, and removable media",
    ),
    Section("Remote Work and Travel", ("AC-17",), "working from outside the organization's sites"),
    Section(
        "Email, Internet, and Social Media",
        ("PL-4(1)",),
        "using email, the internet, social media and external sites",
    ),
    Section(
        "Incident Reporting", ("IR-6",), "reporting a suspected incident, to whom, and how fast"
    ),
    Section(
        "AI Use",
        ("PL-4",),
        "using AI tools: which are approved, and what may not be put into them",
    ),
    Section("Sanctions", ("PS-8",), "what follows when someone breaks these rules"),
    Section(
        "Link to Training",
        ("AT-2", "PL-4"),
        "the awareness training that goes with these rules",
    ),
)

AI_USE_TITLE = "AI Use"


def aup_sections(ai: str | None) -> tuple[Section, ...]:
    """The AUP's sections for the `ai` scope fact.

    `none` leaves AI Use out: an organisation that has no AI to use has no
    rules to write for it, and a section of placeholders is noise a reader
    must wade through. Unset keeps it, since an organisation that hasn't said
    isn't one that has said no; `user` and `builder` keep it too.
    """
    if ai == "none":
        return tuple(s for s in AUP_SECTIONS if s.title != AI_USE_TITLE)
    return AUP_SECTIONS


#: The addendum for administrators: 5923's section 13, kept apart as PL-4's
#: discussion and HHS's Appendix D keep it (5926).
ADDENDUM_SECTIONS: tuple[Section, ...] = (
    Section(
        "Scope",
        ("PL-4",),
        "who holds privileged access and that these rules add to the Acceptable Use Policy",
    ),
    Section(
        "Privileged Access",
        ("AC-6", "PL-4"),
        "using privileged accounts only for the duties that need them, and giving them up",
    ),
)

_PROMPT = register(
    Prompt(
        name="generate.acceptable_use",
        version=1,
        text="""You are a compliance policy drafting engine. \
Draft rules of behavior for the whole workforce of one organization: people \
who have no compliance background and will never see a control ID. Every \
reader signs this document, so each rule must be one they can follow.

Rules:
- Output portable, well-formed CommonMark markdown only: one '##' section for
  each section you are given, in the order given, with exactly that heading.
  No '#' title, no other sections, no Obsidian-style [[wikilinks]].
- Each section: short rules in plain language, as a few bullets or two or
  three sentences, drawn from that section's control text. Say what a person
  must and must not do. Do not restate the control or explain the framework.
- Never cite a framework or a control ID, and never write a citation in
  square brackets.
- Never invent a value the organization decides: how much personal use is
  allowed, which storage, devices or AI tools are approved, a reporting
  deadline, a named team or contact, a specific technology the control does
  not require. Write a square-bracket placeholder instead, e.g.
  `[Approved AI Tools]`, `[Incident Reporting Deadline]`, `[Security Team]`.
- Do not write an acknowledgment, a signature block or a list of controls:
  those are added after you.
""",
    )
)


def fixed_controls(sections=AUP_SECTIONS + ADDENDUM_SECTIONS) -> list[str]:
    """Every control the kind draws on, once, in first-use order."""
    return list(dict.fromkeys(c for section in sections for c in section.controls))


def control_texts(controls, ids) -> dict[str, tuple[str, str]]:
    """{id: (title, statement)} for each id, read from the 800-53 catalog's
    controls, an enhancement from its parent's `enhancements`. An id the
    catalog doesn't hold is left out, and the caller names it."""
    found: dict[str, tuple[str, str]] = {}
    wanted = set(ids)
    for control in controls:
        if control.control_id in wanted:
            found[control.control_id] = (control.title, control.control_statement)
        for enhancement in control.enhancements:
            if enhancement.enhancement_id in wanted:
                found[enhancement.enhancement_id] = (enhancement.title, enhancement.description)
    return found


def _render_sections(sections, texts) -> str:
    parts: list[str] = []
    for section in sections:
        lines = [f"### Section: {section.title}", f"Covers: {section.covers}", ""]
        for control_id in section.controls:
            title, statement = texts[control_id]
            lines.append(f"Control text ({title}):")
            lines.append(statement.strip())
            lines.append("")
        parts.append("\n".join(lines).rstrip())
    return "\n\n".join(parts)


def draft(
    org,
    provider: LLMProvider,
    *,
    title: str,
    sections,
    texts,
    owner: str,
    max_tokens: int = 4096,
) -> str:
    """The model's draft of one document's sections. A failure is raised,
    never swallowed: a document nobody drafted is not written."""
    from policyforge.generate.policy_writer import _render_org
    from policyforge.llm import effort

    lines = [
        _render_org(org),
        "",
        f"Document: {title}",
        f"Owner: {owner}" if owner else "Owner: not recorded; write [Policy Owner] where needed.",
        "",
        "The sections, in order, each with the control text it draws on:",
        "",
        _render_sections(sections, texts),
        "",
        f"Draft the sections of the {title} per the rules above.",
    ]
    response = effort.call(
        provider,
        effort=effort.DRAFTING,
        system=_PROMPT,
        prompt="\n".join(lines),
        temperature=0.2,
        max_tokens=max_tokens,
    )
    return effort.document_text(response, what=title)


_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_ATX_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def _headings(markdown: str) -> list[tuple[int, int, str]]:
    """(line index, level, text) of each ATX heading outside a code fence."""
    found: list[tuple[int, int, str]] = []
    fence = ""
    for index, line in enumerate(markdown.split("\n")):
        marker = _FENCE_RE.match(line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = ""
            continue
        if marker:
            fence = marker.group(1)
        elif atx := _ATX_RE.match(line):
            found.append((index, len(atx.group(1)), atx.group(2).strip()))
    return found


#: Words a heading may add or leave out without naming another section.
_STOP = frozenset(
    {"a", "an", "and", "the", "of", "to", "for", "in", "on", "with", "our", "your"}
    | {"rule", "policy", "section", "part"}
)
#: Spellings that are one word (b5 on PR 453).
_SPELLING = {"acknowledgement": "acknowledgment"}


def _words(text: str) -> frozenset[str]:
    """A heading or title as the words that name it: PR 445's key (numbering
    and emphasis off), a `Section:` prefix off, punctuation and `&` ignored, one
    spelling of each word, a plural read as its singular, and the joining
    words dropped. So `Email, Internet & Social Media`, `Use of AI` and
    `Acknowledgement` read as the sections they name."""
    from policyforge.generate.policy_group import _key

    text = re.sub(r"^(?:section|part|chapter|article)\b[\s\d.:)-]*", "", _key(text))
    words: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text):
        word = _SPELLING.get(word, word)
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        if word not in _STOP:
            words.add(word)
    return frozenset(words)


def _section_of(heading: str, sections, taken: set[str]) -> str | None:
    """The title of the section, not yet filled, that `heading` names.

    A heading holding all of a title's words names it (`Credentials Rules`,
    `Use of AI`), the most specific such title winning. Failing that, a
    heading whose words are all in exactly one title names that one
    (`Training` for Link to Training, `Email and Social Media`)."""
    words = _words(heading)
    if not words:
        return None
    free = [s for s in sections if s.title not in taken]
    holding = [s for s in free if _words(s.title) <= words]
    if holding:
        return max(holding, key=lambda s: len(_words(s.title))).title
    within = [s for s in free if words <= _words(s.title)]
    return within[0].title if len(within) == 1 else None


def _split(markdown: str, sections, title: str):
    """({section title: body}, [what was dropped], [(heading, section) read by
    position]).

    Sections are the headings at the shallowest level where any heading names
    one: `##` as asked, or `#` or `###` when the draft headed them that way.
    A heading is read by its words (`_section_of`). Between two headings read
    that way, in order, the headings whose words name none are read by
    position when there are exactly as many of them as sections left unfilled
    between those two, since the order is fixed and told to the model (b5 on
    PR 453); the run says so. Only inside such a gap: a count over the whole
    draft can't see one section left out and a foreign heading added
    elsewhere, and filed one section's rules under another's heading (b5's
    fuzz, 6381). Any other heading at that level or above is dropped with its
    text, and so is text before the first section, except the document's own
    title."""
    from policyforge.generate.policy_group import _key

    lines = markdown.split("\n")
    heads = _headings(markdown)
    naming = [lvl for _, lvl, text in heads if _section_of(text, sections, set())]
    level = min(naming) if naming else 2
    bounds = [(i, lvl, text) for i, lvl, text in heads if lvl <= level]
    at_level = [position for position, (_, lvl, _) in enumerate(bounds) if lvl == level]

    names: dict[int, str] = {}
    for position in at_level:
        name = _section_of(bounds[position][2], sections, set(names.values()))
        if name is not None:
            names[position] = name
    by_position: list[tuple[str, str]] = []
    order = {section.title: index for index, section in enumerate(sections)}
    read = [(position, order[names[position]]) for position in at_level if position in names]
    for (left, left_index), (right, right_index) in itertools.pairwise(read):
        gap = [p for p in at_level if left < p < right and p not in names]
        filled = set(names.values())
        unfilled = [
            sections[i].title
            for i in range(left_index + 1, right_index)
            if sections[i].title not in filled
        ]
        if gap and len(gap) == len(unfilled):
            for position, section in zip(gap, unfilled, strict=True):
                names[position] = section
                by_position.append((bounds[position][2], section))

    bodies: dict[str, str] = {}
    dropped: list[str] = []
    first = bounds[0][0] if bounds else len(lines)
    if any(
        line.strip() and not (_ATX_RE.match(line) and _key(line.lstrip("# ")) == _key(title))
        for line in lines[:first]
    ):
        dropped.append("(text before the first section)")
    for position, (index, lvl, text) in enumerate(bounds):
        end = bounds[position + 1][0] if position + 1 < len(bounds) else len(lines)
        body = "\n".join(lines[index + 1 : end]).strip()
        if position in names:
            bodies[names[position]] = body
        elif not (lvl < level and _key(text) == _key(title) and not body):
            dropped.append(text)
    return bodies, dropped, by_position


@dataclass
class Assembled:
    """One document, and what assembling it changed."""

    text: str
    #: Sections the draft gave no body: each has a placeholder.
    missing: list[str] = field(default_factory=list)
    #: Headings the draft wrote that aren't the document's, dropped.
    dropped: list[str] = field(default_factory=list)
    #: (heading, section) pairs read by position, not by their words.
    by_position: list[tuple[str, str]] = field(default_factory=list)
    #: Tag-shaped spans taken out of the draft, as written.
    tags: list[str] = field(default_factory=list)


def assemble(
    draft_text: str,
    *,
    title: str,
    sections,
    crosswalk: dict | None,
    acknowledgment: bool,
) -> Assembled:
    """The document: code's title and headings in the fixed order, the
    draft's body for each, then the acknowledgment block (the main document
    only) and the appendix."""
    from policyforge.content.traceability import strip_tag_shaped

    text, tags = strip_tag_shaped(draft_text)
    bodies, dropped, by_position = _split(text, sections, title)

    parts = [f"# {title}", ""]
    missing: list[str] = []
    for section in sections:
        body = bodies.get(section.title, "")
        if not body:
            missing.append(section.title)
            body = MISSING_SECTION.format(title=section.title)
        parts += [f"## {section.title}", "", body, ""]
    if acknowledgment:
        parts += [acknowledgment_block(title), ""]
    parts += [appendix(sections, crosswalk), ""]
    return Assembled(
        text="\n".join(parts).rstrip() + "\n",
        missing=missing,
        dropped=dropped,
        by_position=by_position,
        tags=tags,
    )


def acknowledgment_block(title: str) -> str:
    """What PL-4 b asks for: a documented acknowledgment, made before access.
    Empty fields to sign, not placeholders: the organisation doesn't fill
    them, each reader does."""
    return "\n".join(
        [
            f"## {ACKNOWLEDGMENT_HEADING}",
            "",
            f"I have read and understood the {title} and agree to follow it as a "
            "condition of my access to the organization's systems and information.",
            "",
            "- Name:",
            "- Date:",
            "- Signature:",
            "- Version of this document:",
        ]
    )


def appendix(sections, crosswalk: dict | None) -> str:
    """Each section's 800-53 controls, and what the crosswalk maps each to.

    Read from the crosswalk the run was given, never written here: a
    requirement of a licensed catalog appears only when the organisation
    mapped its own copy. Without a crosswalk the appendix says so."""
    lines = [
        f"## {APPENDIX_HEADING}",
        "",
        "Each section above and the controls it rests on.",
        "",
    ]
    for section in sections:
        mapped: list[str] = []
        for control_id in section.controls:
            for framework, ids in sorted(((crosswalk or {}).get(control_id) or {}).items()):
                mapped += [f"{framework} {i}" for i in ids if f"{framework} {i}" not in mapped]
        entry = "NIST SP 800-53 " + ", ".join(section.controls)
        if mapped:
            entry += "; " + ", ".join(mapped)
        lines.append(f"- **{section.title}**: {entry}")
    if crosswalk is None:
        lines += [
            "",
            "No crosswalk was read, so only the 800-53 controls are listed. Run "
            "`policyforge map` to add what the organization's other catalogs map them to.",
        ]
    return "\n".join(lines)

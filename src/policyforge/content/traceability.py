"""A Procedure's traceability: which Standard section each step carries out.

The user's review of a real 20-topic run (2026-10-10): *"The citations in the
procedures documents are not necessary. I should be able to trace the section
of the procedure back to the standards documents that shows the traceability
on its own. ... It should be in another section."* And, to 80: *"The procedure
does not need line-by-line citations. It should be in another section."*

So a generated Procedure's steps carry no inline framework tags. One separate
section, `## Traceability`, has a line for each step: the step, the Standard
section it implements, and the citations the step carried. **The citations
move; they are not dropped** (80's ruling on #22, comment 5944). Every reader
of tags scans the whole body, so `satisfies`, `drift` and `check` find a
traced Procedure by its own citations exactly as they found it before.

**The mapping is computed, not written by the model.** The drafting prompt
still asks for a tag on each step heading, because the tag is the key: a step
and a Standard section that cite the same requirement are the same
requirement. `trace_procedure` reads those tags with the one reader
`satisfies` and `check` use (`topics.satisfies.parse_citations`, resolved
against the run's `CatalogScope`), maps each step to the Standard sections
that cite any requirement it cites, and writes the section. A model asked to
name a Standard section would be naming one it had never been shown:
`generate_procedure` is given the Standard's title, not its text.

**A list, not a table, and the tags are written as they were drafted.** A
merged tag holds a `|` (`[NIST 800-53 AC-7 | ARC-AMPE AC-7]`), which a table
cell must escape. Every reader that compares tags compares them as strings
(`check._check_citations` against the synthesis, the edit path against the
revision), so an escaped tag would read as a different one and report the
original lost.

**Only a bracket that resolves is moved.** `SOURCE_TAG_RE` matches by shape,
and a role written in that shape (`[Tier 2 Support]`, `[FedRAMP 3PAO]`) is a
placeholder to fill, not a citation. Moving it would delete the sentence's
only statement of who does the step. `is_citation_tag` is the same decision
`generate` already makes when it lists unresolved brackets, so a bracket that
is left behind here is one the run reports there. **Measured on the
user's 20-topic run (local qwen3:14b, 346 bracketed tags over 20
Procedures, 2026-10-10): 15 tags in 7 Procedures stay in the steps**, nearly
all range notation (`[HIPAA 164.310(a)(1)-(2)]`, `[NIST 800-53 SA-4(1)-(2)]`).
They name no requirement the catalogs hold. A range is not a placeholder, but
nothing here can tell it from one by shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .deontic import cites_playbook
from .tags import SOURCE_TAG_RE, is_citation_tag

#: The section's heading, one place for the writer and for the reader.
TRACEABILITY_HEADING = "Traceability"

#: What a line says for a step no Standard section answers. Said in the
#: document, not only on the console: a Procedure that leaves with a gap
#: should read as having one.
NO_MATCH = "No matching Standard section"

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_SECTION_RE = re.compile(rf"^##[ \t]+{TRACEABILITY_HEADING}[ \t]*$", re.IGNORECASE | re.MULTILINE)

#: A tag with the backticks around it, or without. Paired or absent: an
#: unpaired backtick belongs to a code span that holds more than the tag, and
#: removing it would break the span. The whitespace before is taken too, so
#: "Set the limit [NIST AC-7]." reads "Set the limit.".
_TAG_RE = re.compile(
    r"[ \t]*(?:`(?P<ticked>"
    + SOURCE_TAG_RE.pattern
    + r")`|(?P<bare>"
    + SOURCE_TAG_RE.pattern
    + r"))"
)


@dataclass(frozen=True)
class Traced:
    """A Procedure after tracing."""

    text: str
    #: The Procedure sections no Standard section answers, as titled.
    unmatched: tuple[str, ...] = ()
    #: How many Procedure sections were looked at.
    sections: int = 0
    #: How many inline tags were moved into the traceability section.
    moved: int = 0
    #: Whether the document now has a traceability section.
    written: bool = False
    #: Citation tags that resolve and are still inline, outside the
    #: traceability section: steps the model did not write as `###` sections,
    #: or a tag in a section that is not a step. `generate` says so.
    left: int = 0


@dataclass(frozen=True)
class _Section:
    title: str
    level: int
    text: str


def _split(body: str) -> list[_Section]:
    """`body` cut at every heading outside a code fence.

    A section's text is its heading line through the line before the next
    heading **of any level**, so a `##` section's text stops where its first
    `###` begins. Callers that want a section with its children join them.
    Joined with a newline, the sections' texts are the body's lines again.
    """
    sections: list[_Section] = []
    title, level, lines = "", 0, []
    fence = ""
    for line in body.splitlines():
        marker = _FENCE_RE.match(line)
        if marker:
            if not fence:
                fence = marker.group(1)[0] * 3
            elif marker.group(1).startswith(fence):
                fence = ""
        heading = None if fence or marker else _HEADING_RE.match(line)
        if heading:
            if lines:
                sections.append(_Section(title, level, "\n".join(lines)))
            title, level, lines = heading.group("title"), len(heading.group("hashes")), [line]
        else:
            lines.append(line)
    if lines:
        sections.append(_Section(title, level, "\n".join(lines)))
    return sections


def strip_tags(text: str, scope) -> tuple[str, list[str]]:
    """`text` without the citations that resolve in `scope`, and those tags.

    The tags are returned as written, in order. Brackets that do not resolve
    stay: see the module docstring.
    """
    taken: list[str] = []

    def drop(match: re.Match) -> str:
        tag = match.group("ticked") or match.group("bare")
        if not is_citation_tag(tag, scope):
            return match.group(0)
        taken.append(tag)
        return ""

    return _TAG_RE.sub(drop, text), taken


def strip_tag_shaped(text: str) -> tuple[str, list[str]]:
    """`text` without every span shaped like a framework tag, resolved or not,
    and those spans as written.

    What `strip_tags` leaves: a span that doesn't resolve in the run's
    catalogs (`[NIST AC-99]`, `[HITRUST 01.a]`) is still tag-shaped, and a
    document that carries no inline tags has none of them either. The caller
    names what this took, since none of it was checked against a catalog.
    """
    taken: list[str] = []

    def drop(match: re.Match) -> str:
        taken.append(match.group("ticked") or match.group("bare"))
        return ""

    return _TAG_RE.sub(drop, text), taken


def _cited(text: str, scope) -> set[tuple[str, str]]:
    """The requirements `text` cites, as `satisfies` resolves them.

    Keyed by the catalog and requirement a citation stands for, so
    `[NIST SC-8]` and `[NIST 800-53 SC-8]` are one requirement. A citation
    that resolves to nothing is kept as written, so two sections that cite
    the same unknown id still meet.
    """
    from policyforge.topics.satisfies import parse_citations

    keys: set[tuple[str, str]] = set()
    for framework, requirement_id, _, _ in parse_citations(text, scope.names, scope.index, scope):
        catalog, standing_for = scope.standing_for(framework, requirement_id)
        if standing_for:
            keys.update((catalog, resolved) for resolved in standing_for)
        else:
            keys.add((" ".join(framework.split()).casefold(), requirement_id))
    return keys


def _clean_title(title: str, scope) -> str:
    return " ".join(strip_tags(title, scope)[0].split())


def _standard_sections(standard: str, scope) -> list[tuple[str, set[tuple[str, str]]]]:
    """(title, citations) for each section of the Standard below its title.

    **A section's citations are the ones in its own text, not its
    children's.** A Standard that nests `### 4.2 Access Control` under
    `## 4. Procedures` is answered by 4.2, the most specific heading that
    holds the citation, and not by 4. A section title that occurs under two
    parents is shown with its parent, so a line never names a heading the
    reader would find twice.
    """
    heads: list[tuple[str, str, set[tuple[str, str]]]] = []  # (parent, title, cites)
    stack: list[tuple[int, str]] = []
    for section in _split(standard):
        if section.level < 2:
            stack = []
            continue
        while stack and stack[-1][0] >= section.level:
            stack.pop()
        title = _clean_title(section.title, scope)
        parent = stack[-1][1] if stack else ""
        stack.append((section.level, title))
        heads.append((parent, title, _cited(section.text, scope)))
    repeated = {t for _, t, _ in heads if sum(1 for _, u, _ in heads if u == t) > 1}
    return [
        (f"{parent}: {title}" if parent and title in repeated else title, cites)
        for parent, title, cites in heads
        if cites
    ]


def _left_inline(text: str, scope) -> int:
    """Citations that resolve in `scope` and sit outside the traceability section.

    What this counts is the part of the Procedure `trace_procedure` did not
    reach: a step the model wrote as a numbered list or under `####` is not a
    `###` section, so it is not found as a step, and its tags stay where they
    were. Counted over the text returned, so it is what the reader will see.
    """
    found = _SECTION_RE.search(text)
    body = text[: found.start()] if found else text
    return sum(1 for tag in SOURCE_TAG_RE.findall(body) if is_citation_tag(tag, scope))


def trace_procedure(procedure: str, standard: str, standard_title: str, scope) -> Traced:
    """`procedure` with its step citations moved into a traceability section.

    `standard` is the Standard's body, without frontmatter. `scope` is the run's
    `CatalogScope` (`cli.documents._run_scope`), the one `generate` already
    reads brackets against. A document that already has the section is
    returned as it is: a second pass would find no tags in the steps and call
    every one unmatched.

    A Procedure whose steps carry no citation and reach no Standard section
    is returned unchanged: there is nothing to say, and a section of nothing
    but gaps would read as a traceability section.
    """
    if _SECTION_RE.search(procedure):
        return Traced(procedure, left=_left_inline(procedure, scope))

    sections = _split(procedure)

    # The steps: the `###` sections under "Procedure Steps", or every `###`
    # section when the model did not name that heading. A step owns the
    # deeper headings beneath it. `owner[i]` is the step section `i` belongs
    # to, or -1.
    step_at: list[int] = []  # index into `sections` of each step's heading
    owner = [-1] * len(sections)
    in_steps = False
    saw_steps_heading = False
    candidates: list[tuple[int, bool]] = []  # (section index, under "Procedure Steps")
    for i, section in enumerate(sections):
        if section.level == 2:
            in_steps = section.title.strip().casefold() == "procedure steps"
            saw_steps_heading = saw_steps_heading or in_steps
        elif section.level == 3:
            candidates.append((i, in_steps))
    wanted = {i for i, under in candidates if under or not saw_steps_heading}
    current = -1
    for i, section in enumerate(sections):
        if section.level <= 3:
            current = -1
        if i in wanted:
            current = len(step_at)
            step_at.append(i)
        if current >= 0 and section.level >= 3:
            owner[i] = current

    standard_sections = _standard_sections(standard, scope)
    titles: list[str] = []
    answers: list[str] = []
    for step, head in enumerate(step_at):
        text = "\n".join(s.text for i, s in enumerate(sections) if owner[i] == step)
        cited = _cited(text, scope)
        named = [name for name, theirs in standard_sections if cited & theirs]
        titles.append(_clean_title(sections[head].title, scope))
        answers.append("; ".join(named))

    # Each step's tags come out of its own sections, in document order.
    tags: list[list[str]] = [[] for _ in step_at]
    pieces: list[str] = []
    for i, section in enumerate(sections):
        if owner[i] >= 0:
            text, taken = strip_tags(section.text, scope)
            tags[owner[i]] += [t for t in taken if t not in tags[owner[i]]]
            pieces.append(text)
        else:
            pieces.append(section.text)

    unmatched = tuple(t for t, answer in zip(titles, answers, strict=True) if not answer)
    moved = sum(len(t) for t in tags)
    if not step_at or (not any(answers) and not moved):
        return Traced(procedure, unmatched, len(step_at), left=_left_inline(procedure, scope))

    lines = [
        "",
        f"## {TRACEABILITY_HEADING}",
        "",
        f"Each section of this Procedure is listed with the section of {standard_title} "
        "it implements and the citations it carries.",
        "",
    ]
    for title, answer, cited_tags in zip(titles, answers, tags, strict=True):
        said = f"implements {answer}" if answer else NO_MATCH
        line = f"- **{title}**: {said}."
        # A Playbook citation is NIST's suggestion and may sit only in a
        # sentence NIST is the subject of (#300; `playbook_obligations`). So it
        # gets its own, after the section named: a bare tag after "1.
        # Governance." would be read as that fragment's citation, and reported.
        # The other citations keep to the line as written.
        plain = [t for t in cited_tags if not cites_playbook(t)]
        playbook = [t for t in cited_tags if cites_playbook(t)]
        if plain:
            line += " " + " ".join(plain)
        if playbook:
            line += " NIST suggests the actions this section draws on " + " ".join(playbook) + "."
        lines.append(line)
    text = "\n".join(pieces).rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    return Traced(
        text, unmatched, len(step_at), moved, written=True, left=_left_inline(text, scope)
    )

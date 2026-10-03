"""Is every obligation in a generated document backed by its synthesis?

`check.py:_check_citations` verifies that the *citations* in a synthesis
survive into the document it produced. Nothing verifies that the *content*
does. `evals/runner._tags` compares control **references** — so it asks
*"does this document cite what the synthesis cited, and nothing more?"* and
never *"does this document say what the synthesis said?"* — and a
requirement written under a tag the synthesis genuinely carries would be
indistinguishable from one the synthesis wrote (#174).

Two findings come out of that, and **they are different kinds of claim**:

- **Unanchored** — a binding sentence with no citation, in a section whose
  other sentences have one. Deterministic, free, and a **fact**: the
  document either says it or it does not, and the answer is the same twice.
- **Ungrounded** — a cited binding sentence its own synthesis requirements
  do not carry. A model's **opinion**, costing one call per sentence.

**Only the first may gate an exit code.** That is the product ruling on
#196: a malformed document is a fact anyone can verify with the same result
twice; a model's verdict can differ between runs on identical input, and
putting the second behind the same number as the first changes what a
non-zero exit means for every caller that already relies on it. The second
half is a report, and it is the half #174 leaves open rather than closes.

**The tag is an index here, not the assertion.** That is the whole upgrade
over comparing reference sets.

**Results are structured, and any string a caller matches on is derived from
them rather than parsed back out.** `_entailment_warnings` returns formatted
strings that `evals/runner.py` recovers with `startswith(PREFIX)`; change the
wording and the filter stops matching, the harness reports zero findings and
looks healthy. That is the same shape as a regex that cannot match, and it
is why nothing here travels as prose.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Sections start at every markdown heading, read by `_section_starts` from
#: the one classifier (#352). Deliberately not a *numbered* heading:
#: requiring `\d+(\.\d+)*` followed by whitespace rejects `## 1. Purpose`,
#: because the character after `1` is `.` and not a space. Measured on two
#: real generated documents, that single omission found zero sections in one
#: and let the last section run to end-of-file in the other.


@dataclass(frozen=True)
class Requirement:
    """One synthesis requirement, and the controls it was drawn from."""

    text: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Claim:
    """One binding sentence in a generated document."""

    text: str
    line: int
    tags: tuple[str, ...]
    #: Heading text, not a sentence (#352).
    heading: bool = False

    @property
    def anchored(self) -> bool:
        return bool(self.tags)


@dataclass(frozen=True)
class Unanchored:
    """A binding sentence with no citation, where its neighbours have one.

    **A fact, not an opinion**, which is why it may gate: no model is
    consulted and the answer does not move between runs.
    """

    claim: Claim

    def __str__(self) -> str:
        return f"line {self.claim.line}: binds but cites nothing — {self.claim.text[:70]!r}"


@dataclass(frozen=True)
class Ungrounded:
    """A cited binding sentence its own synthesis requirements do not carry."""

    claim: Claim
    #: The requirements the tag indexed, which is what was judged.
    premises: tuple[Requirement, ...] = ()
    reason: str = ""

    def __str__(self) -> str:
        markers = "".join(self.claim.tags)
        detail = f" — {self.reason}" if self.reason else ""
        return f"line {self.claim.line}: {markers} does not carry {self.claim.text[:60]!r}{detail}"


def parse_synthesis(text: str) -> list[Requirement]:
    """The requirement list a synthesis states, one per bullet.

    A synthesis is a markdown bullet list, one requirement per bullet, each
    ending in a source tag — what `synthesis/merge.py` is told to produce.
    Parsed **by bullet rather than by sentence**, because a requirement may
    legitimately contain a full stop and splitting on one would produce
    fragments that ground nothing.
    """
    from .tags import source_tags

    requirements: list[Requirement] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(("-", "*")):
            continue
        body = line[1:].strip()
        if body:
            requirements.append(Requirement(text=body, tags=tuple(source_tags(body))))
    return requirements


def claims(body: str) -> list[Claim]:
    """Every binding sentence in a document, with the citations it carries.

    Segmentation and modality come from `content.deontic.analyze`, which
    already blanks headings to keep line numbers pointing at the file a
    reader has open, and already attaches a citation trailing its sentence
    on the next line backwards — a shape generated Standards have and a
    naive split gets wrong.

    **The tags are read from the source lines, not from `Statement.text`.**
    `analyze` strips the citation out of the sentence and records only a
    boolean, which answers *"is this cited?"* and not *"cited to what?"* —
    and the join needs the identifier. So each statement is credited with
    the tags on the lines it spans, from its own line up to the line before
    the next statement starts.
    """
    from .deontic import analyze, heading_statements
    from .tags import source_tags

    lines = body.splitlines()
    statements = analyze(body)
    found: list[Claim] = []
    for index, statement in enumerate(statements):
        if not statement.binds:
            continue
        following = statements[index + 1].line if index + 1 < len(statements) else len(lines) + 1
        last = max(statement.line, following - 1)
        span = "\n".join(lines[statement.line - 1 : last])
        found.append(Claim(text=statement.text, line=statement.line, tags=tuple(source_tags(span))))
    # A heading that states an obligation is one (80's ruling on #352): its
    # own citations are the ones on its heading text.
    found += [
        Claim(text=h.text, line=h.line, tags=tuple(source_tags(h.text)), heading=True)
        for h in heading_statements(body)
        if h.binds
    ]
    return sorted(found, key=lambda c: c.line)


def _section_starts(body: str) -> list[int]:
    """The line each section starts on: every heading, ATX or setext, as
    `deontic._line_kinds` classifies them (#352). This used an ATX-only
    regex of its own, so a setext heading did not start a section here.

    **Read from the classifier, not from `heading_statements`** (ba on
    #357): that drops a heading with no text, so a bare `##` started no
    section and the obligation under it joined the citing section above.
    Every heading starts one, empty or not: the first line of each run of
    heading text. A test holds this equal to markdown-it's headings.
    """
    from .deontic import _HEADING_TEXT, _SETEXT, _line_kinds

    kinds = _line_kinds(body.split("\n"))
    return [
        n
        for n, kind in enumerate(kinds, start=1)
        if kind in _HEADING_TEXT and not (kind == _SETEXT and n > 1 and kinds[n - 2] == _SETEXT)
    ]


def _section_of(line: int, boundaries: list[int]) -> int:
    """The heading line a given line sits under, or 0 before the first."""
    current = 0
    for start in boundaries:
        if start > line:
            break
        current = start
    return current


def unanchored(body: str) -> list[Unanchored]:
    """Obligations that bind while citing nothing, where their section cites.

    **The scoping is the whole check.** An uncited obligation is not
    automatically unanchored: a Standard's own enforcement clause —
    *"Conformance with this Standard is mandatory"* — binds, carries no
    framework tag, and is entirely correct. Reporting those would make this
    wrong far more often than right, and `check.py:_check_uncited` records
    what happens to a check that is wrong nineteen times out of twenty-one.

    So the rule is **relative to the section**. Where a section contains
    cited requirements, an uncited obligation inside it claims the same
    authority without the anchor. Where a section cites nothing at all, it
    is document boilerplate and says so by its company.
    """
    from .deontic import analyze

    found = claims(body)
    boundaries = _section_starts(body)
    citing_sections = {_section_of(c.line, boundaries) for c in found if c.anchored}
    # **A Playbook citation also makes its section "citing"** (80, on #309),
    # though "NIST suggests ..." never binds and so is never a claim above.
    # Without this, the section #300 produces most -- a suggestion, then the
    # organization's own uncited "must" -- never counted as citing, and the
    # adoption a person should confirm went unreported. Only the Playbook:
    # widening it to every non-binding citation would change the rule's
    # reach across all documents.
    statements = analyze(body)
    citing_sections |= {_section_of(s.line, boundaries) for s in statements if s.cites_the_playbook}

    # **A heading that heads nothing is judged where it stands** (1d on #357).
    # The realistic shape is an obligation closing a citing section, then
    # `---`, then `## 2. Next`: the underline makes the obligation a heading
    # that opens an EMPTY section, which never cites, so it was skipped. A
    # heading whose own section holds no statement is judged in the section
    # before it.
    populated = {_section_of(s.line, boundaries) for s in statements}

    def section(claim: Claim) -> int:
        own = _section_of(claim.line, boundaries)
        if claim.heading and own not in populated:
            return _section_of(claim.line - 1, boundaries)
        return own

    return [Unanchored(c) for c in found if not c.anchored and section(c) in citing_sections]


def premises_for(claim: Claim, requirements: list[Requirement]) -> list[Requirement]:
    """The synthesis requirements a claim's citations index.

    **Every requirement sharing any one of the claim's tags.** A document
    sentence may merge two requirements and cite both, and a synthesis
    requirement may be drawn from several controls; demanding an exact
    tag-set match would miss both and report a grounded sentence as
    ungrounded — the direction that teaches people to ignore a check.
    """
    wanted = set(claim.tags)
    return [r for r in requirements if wanted.intersection(r.tags)]


def judgeable(body: str, synthesis: str) -> list[tuple[Claim, list[Requirement]]]:
    """Cited claims paired with the requirements they will be judged against.

    Separate from the judging so the **cost is knowable before anything is
    spent**: one model call per pair, counted without calling anything.
    """
    requirements = parse_synthesis(synthesis)
    pairs = []
    for claim in claims(body):
        if not claim.anchored:
            continue
        premises = premises_for(claim, requirements)
        if premises:
            pairs.append((claim, premises))
    return pairs


def ungrounded(body: str, synthesis: str, entailer) -> list[Ungrounded]:
    """Cited obligations their own synthesis requirements do not carry.

    **Opinions, and never a gate.** Each is one model call, and a model's
    verdict can differ between runs on identical input — so these are
    reported beside the facts and labelled, never folded into them and never
    behind an exit code.

    **The premises are judged together, not one at a time.** A generated
    sentence legitimately merges two synthesis requirements and cites both,
    so asking *"does requirement A alone carry this?"* would report a
    faithful merge as ungrounded. The answering path judges per passage and
    accepts one supporting passage, because there a citation points at a
    passage that stands alone; here a citation points at requirements the
    generator was told to combine. Same machinery, different question, and
    the difference is why this does not simply call `unsupported_claims`.

    It also makes the cost exactly one call per cited claim, which is what
    `judgeable` counts and what the caller is shown before anything runs.
    """
    findings: list[Ungrounded] = []
    for claim, premises in judgeable(body, synthesis):
        premise = "\n".join(r.text for r in premises)
        verdict = entailer.entails(premise, claim.text)
        if verdict.supports:
            continue
        findings.append(
            Ungrounded(
                claim=claim,
                premises=tuple(premises),
                reason=verdict.reason,
            )
        )
    return findings

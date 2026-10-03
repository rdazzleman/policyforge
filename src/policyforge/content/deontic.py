"""How strongly a document states its requirements, and whether that
strength survived the trip from the control that justifies it.

An assessor does not read `must` and `should` as variations in tone. One is
a commitment the organization can be held to and the other is an intention,
and a Standard full of the second is not auditable. The source controls are
written in obligation language — 800-53 says an organization *shall* — so a
generated Standard that renders a cited requirement as "teams should
consider recertifying" has quietly downgraded a control while still
displaying its citation. The document looks correct, cites correctly, and
promises less than the framework it claims to satisfy.

That is the finding this module exists for, and it is narrower than
counting modal verbs. A `should` on its own is often right: a Policy states
principles, a Standard's commentary can advise, and flagging every one of
them would bury the case that matters under noise nobody reads — the same
trap `zardoz/answer.py` keeps warning about with its integrity checks.
What is reported is a sentence that carries a framework citation and does
not bind.

Deterministic on purpose. Every grader in this project is a substring, a
pattern or a rule, for the reason `evals/runner.py` gives: a checker that
needed a model would have the failure mode it exists to detect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt

from .tags import SOURCE_TAG_RE

#: A sentence binds the organization to something.
OBLIGATION = "obligation"
#: A sentence binds the organization *not* to something. Also binding.
PROHIBITION = "prohibition"
#: Advice. Real content, no commitment.
RECOMMENDATION = "recommendation"
#: Explicit latitude — the reader may choose.
PERMISSION = "permission"
#: A statement of fact, a heading, a definition.
NONE = "none"

#: Binding modalities. An assessor can ask for evidence against these and
#: nothing else.
BINDING = frozenset({OBLIGATION, PROHIBITION})

#: Framework citations, the same shape `edit/apply.py` protects when it
#: rewrites a page. A sentence carrying one is claiming to implement a
#: control, which is what makes its modality checkable at all. Decided in
#: `content/tags.py`, once, for every reader.
_CITATION_RE = SOURCE_TAG_RE

#: Ordered strongest first, because the longer forms contain the shorter
#: ones: "must not" must be tested before "must", or every prohibition in
#: the corpus reads as an obligation to do the thing it forbids.
_MODALITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        PROHIBITION,
        re.compile(
            r"\b(?:must not|shall not|may not|must never|shall never|"
            r"is prohibited|are prohibited|is forbidden|are forbidden|"
            r"is not permitted|are not permitted|is not allowed|are not allowed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        OBLIGATION,
        re.compile(
            r"\b(?:must|shall|is required to|are required to|is obligated to|"
            r"are obligated to|will be required to|"
            r"is required|are required|is mandatory|are mandatory)\b",
            re.IGNORECASE,
        ),
    ),
    (
        RECOMMENDATION,
        re.compile(
            r"\b(?:should|ought to|is recommended|are recommended|"
            r"is encouraged|are encouraged|is advised|are advised)\b",
            re.IGNORECASE,
        ),
    ),
    (
        PERMISSION,
        re.compile(
            r"\b(?:may|can elect to|is permitted|are permitted|is allowed|"
            r"are allowed|at the discretion of|optional)\b",
            re.IGNORECASE,
        ),
    ),
)

#: Markdown emphasis, stripped before matching for the reason
#: `zardoz/answer.py` strips it: a generated Standard writes "**must**",
#: and a checker that cannot see through the asterisks reports the one
#: sentence that got it right.
_MARKUP_RE = re.compile(r"\*\*|__|\*|`")


def _sentences(text: str) -> list[tuple[int, str]]:
    """`(offset, piece)` for each sentence, using the project's boundary rule.

    **This split every citation containing a dot until 2026-09-21.** The
    rule was `[^.!?]+(?:[.!?]+|$)`, which breaks at every full stop —
    including the ones *inside* an identifier. `[HIPAA Security Rule
    164.308(a)(3)(i)]` became three fragments, none of which is a
    citation, so `cited` was False on every statement and
    `weakened_citations` reported nothing.

    **The direction of that failure is silence.** A document whose
    requirements were all correctly cited and all written as "should
    consider" read as clean. Measured at 05e5046 (control and enhancement
    ids in every shipped `controls.json`): 914 of 2,434 identifiers contain
    a dot, and six catalogs are **entirely** dotted, including HIPAA. When
    this was fixed it was 324 of 1,844 and four; both catalogs added since
    (ONC 170.315, the AI RMF Playbook) are entirely dotted. The check worked
    for 800-53, FedRAMP and ARC-AMPE, and not for the framework this
    product exists to serve.

    `entail/base.py` replaced the same pattern for the same reason and
    its docstring carries the argument, including why a rule that
    special-cased digit-dot-digit would leave `45 C.F.R. 164.312`
    broken. Imported rather than restated: two copies of one rule, one of
    them fixed, is how this defect existed at all. Found by
    policyforge-b5, who wrote that fix and then found its sibling still
    carrying the bug.

    Returns offsets because a `Statement` carries the line it came from
    and `entail`'s `_segments` does not need them.
    """
    from policyforge.entail.base import _BOUNDARY_RE, _ends_an_initial, _starts_with_a_capital

    pieces: list[tuple[int, str]] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        if _ends_an_initial(text, match.start()) and _starts_with_a_capital(text, match.end()):
            continue
        pieces.append((start, text[start : match.end()]))
        start = match.end()
    pieces.append((start, text[start:]))
    return [(offset, piece) for offset, piece in pieces if piece.strip()]


@dataclass(frozen=True)
class Statement:
    """One sentence, and what it commits the organization to."""

    line: int
    text: str
    modality: str
    cited: bool
    #: The citation tags this sentence carries, including one trailing it on
    #: the next line. Recorded so a check can ask WHICH framework a sentence
    #: cites, not only whether it cites one (#300).
    citations: tuple[str, ...] = ()
    #: Heading text, not a sentence (#352): reported by the same checks,
    #: with a message that says so, because rendered output hides it.
    heading: bool = False

    @property
    def binds(self) -> bool:
        return self.modality in BINDING

    @property
    def cites_the_playbook(self) -> bool:
        """Any of its citations names the NIST AI RMF Playbook."""
        return any(_is_playbook(part) for tag in self.citations for part in _parts(tag))

    @property
    def cites_only_the_playbook(self) -> bool:
        """It cites the Playbook, and nothing that states an obligation.

        The unit both Playbook rules use (80, on #300). A sentence that also
        cites a binding source carries that source's obligation, so the
        binding source's strength rule governs it, and binding is correct.

        **The AI RMF Core does not count as binding** (#341, 80's ruling). It
        states outcomes, so a Playbook sentence that also cites the Core is
        still NIST's suggestion and still read by the gate. The non-binding
        set is `crosswalk.overlay.NON_BINDING_FRAMEWORKS`, not a list here.
        The name is kept because every caller asks the same question it
        always did: is this sentence governed by the Playbook rules?
        """
        from policyforge.crosswalk.overlay import NON_BINDING_FRAMEWORKS
        from policyforge.mapping.crosswalk import normalize_framework

        parts = [part for tag in self.citations for part in _attributed_parts(tag)]
        if not parts:
            return False
        frameworks = [normalize_framework(part.framework or "") for part in parts]
        return "nist-ai-rmf-playbook" in frameworks and all(
            f in NON_BINDING_FRAMEWORKS for f in frameworks
        )

    @property
    def weakens_a_citation(self) -> bool:
        """Claims a control and promises less than one.

        A sentence with no citation is not making a claim this can judge,
        and a binding sentence has nothing wrong with it. The overlap is
        the finding.

        **Except a sentence citing only the Playbook**, which is meant not to
        bind: NIST's suggestions are voluntary, so "NIST suggests ..." is the
        correct rendering and must not be reported as a weakened requirement
        (#300). Without this, the correct form warned and the wrong form,
        "must ...", passed.
        """
        if self.cites_only_the_playbook:
            return False
        return self.cited and not self.binds


#: How a Playbook citation begins, as the catalog's framework name is written
#: into tags: `[NIST AI RMF Playbook Govern 1.1 Action 1]`.
_PLAYBOOK = "NIST AI RMF Playbook "


#: The Playbook's framework name as written into tags, without the space.
_PLAYBOOK_NAME = _PLAYBOOK.strip()


def _parts(tag: str) -> list[str]:
    """One tag's references, a Playbook shorthand part written out in full (#333).

    Split by `content/tags.tag_parts`, the one function `satisfies` also
    uses, against the one list of framework names both read
    (`tags.known_framework_names`, every catalog on disk). A part naming
    another framework -- `NIST 800-53 AC-2`, or an abbreviation that resolves
    to exactly one known catalog, `HIPAA 164.308(a)(1)` -- is that framework's
    and not inherited. A part naming none inherits the part before it (#333).

    **An inherited Playbook part is the Playbook's whether or not its id
    resolves** (80's ruling on #340). #337 counted it only if it resolved, so
    an invented `Govern 9.9 Action 1` left the sentence mixed, and the gate
    never read it: adding one bogus id turned an ERROR into a strength
    warning (1d). Now the sentence stays Playbook-only, and `check` reports
    the unresolvable part as its own ERROR (`unresolved_playbook_parts`).
    """
    out: list[str] = []
    for part in _attributed_parts(tag):
        out.append(_PLAYBOOK + part.rest if part.framework == _PLAYBOOK_NAME else part.citation)
    return out


def _attributed_parts(tag: str):
    """`tag_parts` against every known framework name plus the Playbook's,
    with an abbreviation that names exactly one known catalog kept as its
    own framework, as `satisfies` does."""
    from policyforge.mapping.crosswalk import normalize_framework
    from policyforge.topics.satisfies import resolve_framework

    from .tags import known_framework_names, tag_parts

    names = {_PLAYBOOK_NAME, *known_framework_names()}
    keys = {normalize_framework(n): () for n in names}
    return tag_parts(tag, names, lambda word: bool(resolve_framework(word, keys)))


def unresolved_playbook_parts(text: str) -> list[tuple[int, str]]:
    """(line, citation) for each shorthand part that inherits the Playbook
    and names no Playbook subcategory or action (#340, 80's ruling (a)).

    An invented citation in a compliance document is an error on its own
    terms, whatever else the sentence says: `[NIST AI RMF Playbook Govern 1.1
    Action 1 | Govern 9.9 Action 1]` names an action NIST never published.
    **Inherited parts only**, as ruled: a part that writes the Playbook's
    name in full and still fails to resolve is reported by `satisfies` as an
    unknown citation, not here.
    """
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for tag in _CITATION_RE.findall(line):
            for part in _attributed_parts(tag):
                if (
                    part.framework == _PLAYBOOK_NAME
                    and part.inherited
                    and part.rest.lower() not in _playbook_ids()
                ):
                    found.append((number, part.citation))
    return found


def _is_playbook(reference: str) -> bool:
    return reference.startswith(_PLAYBOOK)


#: A sentence framed as NIST's: its subject is NIST or the Playbook. Matched
#: at the start of the sentence, after list markers and emphasis.
_NIST_SUBJECT = re.compile(
    r"^(?:NIST(?:'s)?(?:\s+AI\s+RMF)?(?:\s+Playbook)?"
    r"|The\s+(?:NIST\s+)?(?:AI\s+RMF\s+)?Playbook)\s+(?:also\s+|further\s+)?(\w+)",
    re.IGNORECASE,
)

#: With NIST as subject, the verbs that say NIST obliges. Closed on purpose:
#: it names the one claim the ruling forbids, "NIST requires ...".
_NIST_OBLIGES = frozenset({"requires", "mandates", "obliges", "obligates", "directs"})

_LEADING_MARKER = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)+")

#: The only openers a Playbook sentence may put before its NIST/Playbook
#: subject (#319, 80's corrected ruling). **A closed list of SHAPES, not a
#: free-text phrase checked for commitments**: the first ruling allowed any
#: prepositional phrase without a binding verb, and "For Govern 1.4 the
#: organization will adopt these, NIST suggests ..." passed, because
#: `classify` does not count will, commits or is responsible as binding. That
#: was the default-allow shape #309 rejected, one clause over.
#:
#: **The bound, stated:** at most ONE opener, exactly one of the four shapes
#: below, ending at its comma. `<SUB>` must be a Core subcategory id in the
#: shipped `nist-ai-rmf` catalog (checked in `_opener_end`, so the id slot
#: cannot carry text), and `<N>` is digits. Case and whitespace are free.
#: Anything else before the subject fails: two stacked openers, a comma
#: inside one, a missing comma, and harmless openers not listed here, such as
#: "In practice, NIST suggests ...". Those are false alarms on purpose, loud,
#: and fixed by a reword, which is the safe direction.
_SUB = r"(?P<sub>[A-Za-z]+\s+\d+\.\d+)"
_OPENERS = tuple(
    re.compile(shape, re.IGNORECASE)
    for shape in (
        rf"For\s+{_SUB}\s*,\s*",
        rf"Among\s+the\s+\d+\s+actions\s+(?:NIST\s+suggests|the\s+Playbook\s+lists)\s+for\s+{_SUB}\s*,\s*",
        r"Across\s+(?:these|the\s+\d+)\s+actions\s*,\s*",
        rf"Of\s+the\s+\d+\s+actions\s+for\s+{_SUB}\s*,\s*",
    )
)


def _core_subcategories() -> frozenset[str]:
    """The shipped AI RMF Core's subcategory ids, lower-cased (#319).

    Read once from the bundled catalog, the same one `etl-ai-rmf` pins. An
    installation with no bundled catalogs raises here rather than answering
    "none": an empty set would pass the id-free opener and refuse the rest,
    which is a gate that half works.
    """
    global _SUBCATEGORIES
    if _SUBCATEGORIES is None:
        import json

        from policyforge.scaffold import bundled_root

        rows = json.loads(
            bundled_root()
            .joinpath("frameworks", "nist-ai-rmf", "controls.json")
            .read_text(encoding="utf-8")
        )
        _SUBCATEGORIES = frozenset(
            " ".join(e["enhancement_id"].split()).lower()
            for row in rows
            for e in row.get("enhancements") or []
        )
    return _SUBCATEGORIES


_SUBCATEGORIES: frozenset[str] | None = None


def _opener_end(plain: str) -> int:
    """Where the subject may start: after one allowed opener, else 0."""
    for shape in _OPENERS:
        match = shape.match(plain)
        if not match:
            continue
        sub = match.groupdict().get("sub")
        if sub is not None and " ".join(sub.split()).lower() not in _core_subcategories():
            return 0
        return match.end()
    return 0


#: The shortest run quoted from a cited action that can carry a binding word
#: past the check (#320, 80's ruling). **The bound, stated:** at least this
#: many consecutive words, compared case-insensitively on whitespace-split
#: tokens (so punctuation is part of a word, and "preserved," is not
#: "preserved"), occurring verbatim in the text of a Playbook action the
#: SAME sentence cites, looked up in the shipped catalog. A binding word
#: outside every such run still binds. A paraphrase does not qualify, so a
#: model gets no licence to write its own "must".
#:
#: **The second bound, stated (80's ruling (b) on #325):** a run's FIRST word
#: is never exempted, so a binding word is excused only when at least one
#: word of the same verbatim run comes before it. The first word still counts
#: toward the six. A run starting AT "must" let the writer supply the
#: subject outright: "NIST suggests that the organization must be preserved
#: for fulsome understanding ..." quotes Action 4's words and was passing
#: (1d's finding).
#:
#: **The limit, stated (1d and 9b measured it on #325; 80 ruled it stays a
#: limit, not a second count):** a preceding word keeps the modal inside
#: NIST's clause but does NOT guarantee NIST's subject. When that word is a
#: relative pronoun, the writer can still supply its antecedent, and these
#: pass: "NIST suggests keeping the organization's own records that must be
#: preserved for fulsome understanding or execution", and "... the
#: organization that must be preserved ...". A second word count would only
#: move the smuggle one word right. The exposure is instead pinned by
#: `test_playbook_quoted_must`: the shipped Playbook actions that carry a
#: binding word at all -- today Govern 1.7 Action 4 (must), Measure 2.9
#: Action 6 and Measure 3.2 Action 1 (may not) -- and a catalog that adds one
#: fails that test, so the limit is revisited then.
QUOTE_MIN_WORDS = 6

_PLAYBOOK_ACTIONS: dict[str, list[str]] | None = None
_PLAYBOOK_IDS: frozenset[str] | None = None


def _playbook_ids() -> frozenset[str]:
    """Every id the shipped Playbook catalog holds, lower-cased: its
    subcategories (`govern 1.1`) and their actions (`govern 1.1 action 2`).
    What an inherited shorthand part must resolve to (#333)."""
    global _PLAYBOOK_IDS
    if _PLAYBOOK_IDS is None:
        import json

        from policyforge.scaffold import bundled_root

        rows = json.loads(
            bundled_root()
            .joinpath("frameworks", "nist-ai-rmf-playbook", "controls.json")
            .read_text(encoding="utf-8")
        )
        _PLAYBOOK_IDS = frozenset(
            " ".join(i.split()).lower()
            for row in rows
            for i in [
                row["control_id"],
                *(e["enhancement_id"] for e in row.get("enhancements") or []),
            ]
        )
    return _PLAYBOOK_IDS


def _playbook_actions() -> dict[str, list[str]]:
    """Each shipped Playbook action's text as lower-cased tokens, by id."""
    global _PLAYBOOK_ACTIONS
    if _PLAYBOOK_ACTIONS is None:
        import json

        from policyforge.scaffold import bundled_root

        rows = json.loads(
            bundled_root()
            .joinpath("frameworks", "nist-ai-rmf-playbook", "controls.json")
            .read_text(encoding="utf-8")
        )
        _PLAYBOOK_ACTIONS = {
            " ".join(e["enhancement_id"].split()).lower(): (e.get("description") or "")
            .lower()
            .split()
            for row in rows
            for e in row.get("enhancements") or []
        }
    return _PLAYBOOK_ACTIONS


def _cited_actions(citations) -> list[list[str]]:
    """The token lists of the Playbook actions these citation tags name."""
    actions = _playbook_actions()
    cited = []
    for tag in citations:
        for part in _parts(tag):
            if _is_playbook(part):
                key = " ".join(part[len(_PLAYBOOK) :].split()).lower()
                if key in actions:
                    cited.append(actions[key])
    return cited


def _unquoted(plain: str, quoted_from: list[list[str]]) -> str:
    """`plain` with every run of QUOTE_MIN_WORDS+ words found verbatim in
    one of `quoted_from` blanked, so `classify` sees only the rest."""
    tokens = plain.split()
    lowered = [t.lower() for t in tokens]
    covered = [False] * len(tokens)
    for action in quoted_from:
        for i in range(len(lowered)):
            for j in range(len(action)):
                length = 0
                while (
                    i + length < len(lowered)
                    and j + length < len(action)
                    and lowered[i + length] == action[j + length]
                ):
                    length += 1
                if length >= QUOTE_MIN_WORDS:
                    # From i + 1: the run's first word is never excused.
                    for k in range(i + 1, i + length):
                        covered[k] = True
    return " ".join("_" if c else t for t, c in zip(tokens, covered, strict=True))


#: Where a Playbook sentence can open a second clause (#323, 80's ruling (B)):
#: `, and` / `, but` / `, so`, a semicolon (optionally followed by one of those
#: words) or an em dash. Closed.
_JOINER = r"(?:,\s+(?:and|but|so)\s+|;\s+(?:(?:and|but|so)\s+)?|\s*\u2014\s*(?:(?:and|but|so)\s+)?)"

#: Subjects that commit the organization when they open a clause after a
#: joiner. Closed and case-insensitive. **"management" is deliberately NOT
#: here**: in b5's 66 real glm sentences, "..., and management resources" is
#: a list item, and it was the only false alarm this list produced.
GENERIC_ACTORS = (
    "the organization",
    "the organisation",
    "we",
    "our",
    "us",
    "staff",
    "employees",
    "personnel",
    "the team",
    "the company",
)


def _alternatives(actors) -> str:
    words = [a for a in {" ".join(str(a).split()) for a in actors} if a]
    return "|".join(
        r"\s+".join(re.escape(w) for w in a.split()) for a in sorted(words, key=len, reverse=True)
    )


def _actor_pattern(org_actors=()) -> re.Pattern[str]:
    """Joiner, then an actor, then no further word character.

    **Generic actors match in any case; the organization's own do NOT**
    (1d on #329). Config names are proper nouns, and one-word team or vendor
    names are also ordinary words: with teams "Security", "Compliance", "IT"
    and vendors "Legal", "Privacy", case-insensitive matching fired on 4 of
    the 459 shipped Playbook actions and 3 of b5's 66 sentences, every one an
    Oxford-comma list item ("..., and legal review"). Stated limit: a
    declared name written in another case ("ACME" for "Acme") is not caught.

    A negative lookahead for a word character, rather than a word boundary,
    follows the actor, so a name that ends in punctuation, "Foo (EU)", can
    still match (1d on #329).
    """
    generic = _alternatives(GENERIC_ACTORS)
    declared = _alternatives(org_actors)
    actor = f"(?i:{generic})" + (f"|{declared}" if declared else "")
    return re.compile(rf"(?i:{_JOINER})(?:{actor})(?!\w)")


def _second_clause_actor(plain: str, org_actors=()) -> str | None:
    """The actor opening a clause after a joiner, if it is the organization's.

    **Why not "anything after a joiner must be NIST"** (80's first ruling on
    #323, withdrawn): glm writes LISTS -- "...; establishing ...", "...; and
    determining ...", "..., and stakeholder engagement plans" -- and that rule
    refused 65 of b5's 66 real sentences. What must not appear is the
    organization committing, and the organization's actors are knowable: the
    generic ones above, plus its own name, teams and vendors from config
    (`org_actors`, derived by `org.context.org_actors`, never typed here).

    **The bound, stated:** only the joiners in `_JOINER`, and only an actor
    that is generic or declared. An actor that is neither -- an unlisted
    subsidiary, a person's name -- is NOT caught, and nor is a clause joined
    some other way (a bare comma, "which", "while").
    """
    match = _actor_pattern(org_actors).search(_CITATION_RE.sub("", plain))
    return match.group(0) if match else None


def framed_as_nists(sentence: str, citations=(), org_actors=()) -> bool:
    """Whether a sentence speaks as NIST describing or suggesting (#300).

    **An allow-list on the subject, not a deny-list of obligation verbs**
    (80's ruling, refined after 1d's review). Every round of a verb list
    found another spelling -- "will", "is responsible for", the imperative,
    "ensures", "NIST requires" -- because a deny-list chases paraphrase. So
    the sentence must have NIST or the Playbook as its subject ("NIST
    suggests ...", "The Playbook groups ..."), and anything else fails by
    default: an organization as subject in any wording, an imperative, any
    other subject. Within NIST-as-subject, the verbs that say NIST obliges
    are refused, and so is a sentence that binds anyway ("NIST suggests that
    the organization must ..."), since a Playbook sentence never binds.

    The subject may follow one opener from `_OPENERS` (#319), such as "Among
    the 7 actions NIST suggests for Govern 1.4, NIST suggests ...". The
    binding check below still reads the WHOLE sentence, opener included.

    **Except NIST's own words (#320).** With `citations` given, a binding
    word inside a run of `QUOTE_MIN_WORDS`+ words quoted verbatim from a
    Playbook action those citations name is NIST's text, not the
    organization's obligation, and is not counted. Without citations
    nothing is exempt.

    **And one main clause, NIST's (#323).** A clause after a joiner whose
    subject is the organization -- generic, or declared in config as
    `org_actors` -- fails, whatever its verb: "NIST suggests reviewing the
    inventory, and Acme will adopt it". See `_second_clause_actor`.
    """
    plain = _LEADING_MARKER.sub("", _MARKUP_RE.sub("", sentence).strip())
    match = _NIST_SUBJECT.match(plain[_opener_end(plain) :])
    if not match or match.group(1).lower() in _NIST_OBLIGES:
        return False
    if _second_clause_actor(plain, org_actors):
        return False
    if classify(plain) not in BINDING:
        return True
    return classify(_unquoted(plain, _cited_actions(citations))) not in BINDING


def playbook_tagged_headings(text: str) -> list[tuple[int, str]]:
    """Headings that carry a Playbook citation, as (line, heading) (#309).

    **A heading may not cite the Playbook at all**, even merged with a
    binding source (80's ruling on #309). A heading tag scopes every step
    beneath it, so it turns a suggestion into an instruction by position,
    whatever the heading says. `analyze` blanks headings on purpose, so the
    sentence check never saw these: measured on a generated Procedure, all
    9 of its 9 Playbook tags sat on headings, and the gate reported none.
    """
    found = []
    lines = text.split("\n")
    kinds = _line_kinds(lines)
    for number, line in enumerate(lines, start=1):
        # Headings as `analyze` classifies them (9b on #350): a setext
        # heading's text line, blanked out of sentence analysis, is still
        # a heading here, so an obligation underlined with `---` is caught
        # by this rule rather than by neither.
        if kinds[number - 1] not in _HEADING_TEXT:
            continue
        tags = _CITATION_RE.findall(line)
        if any(_is_playbook(part) for tag in tags for part in _parts(tag)):
            found.append((number, line.strip()))
    return found


def playbook_obligations(text: str, org_actors=()) -> list[Statement]:
    """Sentences citing only the NIST AI RMF Playbook, not framed as NIST's.

    The Playbook is voluntary: NIST's suggested actions, which it
    deliberately kept out of the AI RMF Core. A sentence citing it may say
    NIST suggests or describes; it may never present an action as anyone's
    obligation -- "Acme Health will ... [Playbook ...]" does so as surely as
    "NIST requires ..." (80's rulings on #300 and #309). An obligation the
    organization adopts goes in its own sentence, without the Playbook tag.

    **Only sentences whose every citation is a Playbook tag.** A merged tag
    that also names a binding source -- `[NIST 800-53 AC-2 | NIST AI RMF
    Playbook ...]` -- carries that source's obligation, and the requirement
    strength rule governs it instead.

    **A colon list is judged item by item** (80's ruling (a) on #354). The
    lead-in and its items are one statement for citation crediting (#351),
    but that joining never let one item's citation vouch for a sibling: in
    `Acme Health must:` / `- maintain a register [Playbook]` / `- review
    access [AC-2]` the joined statement cites both, reads as mixed, and the
    Playbook item escaped. So each SENTENCE of each item is judged against its
    own citations (plus any the lead-in or the list itself carries) and
    reported at its line: the item's first sentence as "lead-in + sentence",
    unless it is NIST's speech on its own, and any later sentence alone, as
    in a plain item (1d on #356: one item's 800-53 sentence vouched for its
    Playbook sentence). Under an organization's lead-in this is the verdict
    the same item gets as a plain item, and the tests take that verdict as
    their oracle. A lead-in with NIST as its subject (`NIST suggests:`)
    frames each item's first sentence as NIST's.
    """
    statements = analyze(text)
    units, covered = _colon_units(text, statements, org_actors)
    judged = [s for s in statements if id(s) not in covered] + units
    return sorted(
        (
            s
            for s in judged
            if s.cites_only_the_playbook and not framed_as_nists(s.text, s.citations, org_actors)
        ),
        key=lambda s: s.line,
    )


def _colon_units(
    text: str, statements: list[Statement], org_actors=()
) -> tuple[list[Statement], set[int]]:
    """Each colon list's items as "lead-in + item" statements, and the ids of
    the statements those lists cover (#354)."""
    structure = _structure(text)
    # The lines `analyze` read, list numbers blanked (#365), so the lead-in is
    # found inside its statement by the same text.
    lines = _blanked_lines(text, structure)
    units: list[Statement] = []
    covered: set[int] = set()
    for colon in structure.colon_lists:
        if colon.lead < 0 or not colon.items:
            continue
        lead_line, end_line = colon.lead + 1, colon.end + 1
        before = [s for s in statements if s.line <= lead_line]
        if not before:
            continue
        owner = before[-1]
        owned = [owner] + [s for s in statements if lead_line < s.line <= end_line]
        covered.update(id(s) for s in owned)

        def joined(first: int, last: int) -> str:
            return " ".join(" ".join(lines[first : last + 1]).split())

        first_item = joined(colon.items[0][0], colon.items[0][0])
        at = owner.text.find(first_item)
        lead = owner.text[:at].strip() if at > 0 else joined(colon.lead, colon.lead)
        shared = tuple(_CITATION_RE.findall(lead)) + tuple(
            tag for index in colon.trailing for tag in _CITATION_RE.findall(lines[index])
        )
        for first, last in colon.items:
            # Each SENTENCE of the item, split by `analyze` itself so that its
            # boundaries and trailing-citation credit are a plain item's: one
            # item's 800-53 sentence must not vouch for its Playbook sentence
            # any more than a sibling item may (1d on #356). The lead-in runs
            # on into the item's FIRST sentence only ("Acme Health must: keep
            # a register"); a later sentence stands alone, as it does in a
            # plain item, so `NIST suggests ...` there is still NIST's. So does
            # a first sentence that is NIST's speech on its own: it has its own
            # subject, and the lead-in does not run into it.
            body = "\n".join([_LEADING_MARKER.sub("", lines[first]), *lines[first + 1 : last + 1]])
            for position, sentence in enumerate(analyze(body)):
                own = shared + sentence.citations
                alone = position > 0 or framed_as_nists(sentence.text, own, org_actors)
                unit = sentence.text if alone else f"{lead} {sentence.text}"
                units.append(
                    Statement(
                        line=first + sentence.line,
                        text=unit,
                        modality=classify(unit),
                        cited=bool(shared + sentence.citations),
                        citations=shared + sentence.citations,
                    )
                )
    return units, covered


#: The one modal-free obligation `classify` reads (#363, 80's ruling): an
#: actor "is/are responsible|accountable for" doing something. The subject is
#: taken from the start of its clause (`_DUTY_CLAUSE`) and judged by
#: `_duty_subject_is_actor`; the complement must be a gerund, not a noun
#: ("responsible for any use of" is `none`, 5b's C4 on #360). Kept OUT of
#: `_MODALITY_PATTERNS`, whose alternatives `_HEADING_MODALS` reads as modal
#: phrases.
_DUTY_RE = re.compile(
    r"\b(?:is|are)\s+(?:(?:also|jointly|solely|ultimately|directly)\s+)?"
    r"(?:responsible|accountable)\s+for\s+"
    r"(?!(?:\w*thing|during|morning|evening|ceiling|string|spring|king|ring|wing"
    r"|sibling|offspring)\b)[a-z]{2,}ing\b",
    re.IGNORECASE,
)

#: Where the duty's clause starts: the last of these before "is/are". The
#: joiners are the ones a Playbook sentence may use (`_JOINER`), plus a colon.
_DUTY_CLAUSE = re.compile(r",\s+(?:and|but|so)\s+|;\s*|:\s+|\s*—\s*")

#: Subject heads that are not an actor: a document or a framework (5b's hazard
#: 2 on #363: 90 of 139 "X does Y" hits in Standards had one), a pronoun that
#: may stand for either, or a relative ("a team that is responsible for ...",
#: which is how all 3 of the corpus's NIST-suggests hits read).
_NOT_ACTORS = frozenset(
    {
        "standard", "standards", "policy", "policies", "procedure", "procedures",
        "document", "documents", "process", "processes", "section", "sections",
        "control", "controls", "requirement", "requirements", "rule", "rules",
        "framework", "frameworks", "catalog", "catalogs", "playbook", "nist", "rmf",
        "hipaa", "fedramp", "govramp", "hitrust", "iso",
        "it", "this", "that", "these", "those", "which", "who", "whom", "whose",
    }
)  # fmt: skip


#: One-word departments that are spelled like a gerund. A one-word subject
#: ending in -ing is an activity ("Testing is responsible for ...") unless it
#: is one of these (1d's caution from #367, in both directions).
_DEPARTMENT_GERUNDS = frozenset(
    {
        "engineering", "accounting", "marketing", "purchasing", "nursing", "billing",
        "networking", "manufacturing", "recruiting", "contracting", "licensing",
        "housekeeping", "consulting", "publishing", "banking",
    }
)  # fmt: skip


def _duty_subject_is_actor(subject: str) -> bool:
    """Whether the words before "is/are responsible for" name an actor.

    The head is the last word before any "of" ("The owner of this Standard"
    is headed by "owner"). It fails when it is in `_NOT_ACTORS`, and when the
    whole subject is one gerund ("Testing is responsible for ..."), which is
    an activity, not someone, unless it is a department in
    `_DEPARTMENT_GERUNDS` ("Engineering"). **Stated limits:** a pronoun that
    does stand for a team ("...: it is accountable for implementing ...",
    one sentence in 5b's corpus) is not bound; a one-word department ending
    in -ing that is not listed is not bound; and a document noun inside a
    longer head ("Standard owner") is judged by its last word only.
    """
    head = re.split(r"\s+of\s+", subject.strip(), maxsplit=1, flags=re.IGNORECASE)[0]
    words = re.findall(r"[A-Za-z][\w'-]*", _CITATION_RE.sub("", head))
    if not words:
        return False
    if (
        len(words) == 1
        and words[0].lower().endswith("ing")
        and words[0].lower() not in _DEPARTMENT_GERUNDS
    ):
        return False
    # By case for this one word (1d on #371): "IT" is the department, "It"
    # and "it" the pronoun.
    if words[-1].removesuffix("'s") == "IT":
        return True
    return words[-1].lower().removesuffix("'s") not in _NOT_ACTORS


def _assigns_duty(visible: str) -> bool:
    """Whether an actor is made responsible or accountable for doing something.

    **Never inside NIST's own speech** (#177: the Playbook suggests, never
    requires): a sentence whose subject is NIST or the Playbook, after one
    allowed opener, does not bind through this rule, whatever its later
    clauses say. Its modal words are still read by `classify`, as before.
    """
    plain = _LEADING_MARKER.sub("", visible.strip())
    if _NIST_SUBJECT.match(plain[_opener_end(plain) :]):
        return False
    for match in _DUTY_RE.finditer(plain):
        before = plain[: match.start()]
        starts = [m.end() for m in _DUTY_CLAUSE.finditer(before)]
        subject = before[starts[-1] if starts else 0 :]
        if _ELIDED_RE.search(subject):
            # "X performs A; and is accountable for ensuring B": the subject
            # is the sentence's, so the sentence's opening is what is judged.
            if not _DOCUMENT_OPENING_RE.match(plain):
                return True
        elif _duty_subject_is_actor(subject):
            return True
    return False


#: A clause whose subject is elided: it ends in a conjunction ("...; and is
#: accountable for ..."), which also catches a verb phrase run on without a
#: boundary ("This Standard applies to X and is responsible for ...").
_ELIDED_RE = re.compile(r"\b(?:and|or|but|also|then)\s*$", re.IGNORECASE)

#: A sentence that opens on a document, a framework or a bare pronoun: with an
#: elided subject, that is who the duty would fall on.
_DOCUMENT_OPENING_RE = re.compile(
    r"(?:(?:the|this|that|these|each|every)\s+)?(?:NIST\b|HIPAA\b|FedRAMP\b|GovRAMP\b"
    r"|HITRUST\b|ISO\b|(?-i:[Ii]t\b)|this\b|that\b|(?:\w+\s+){0,2}?(?:standard|policy|procedure"
    r"|document|process|section|playbook|framework)s?\b)",
    re.IGNORECASE,
)


def classify(sentence: str) -> str:
    """The strongest modality the sentence carries.

    Strongest rather than first, because real requirement prose nests
    latitude inside obligation: "records must be retained in written form
    (which may be electronic)" is an obligation with a permitted detail,
    not a permission. Reading it as the latter would report the firmest
    sentence in the document as its weakest.

    **Obligations without a modal: one family binds, the rest do not, by
    ruling** (80 on #363, from 5b's count over 83 generated Standards and 45
    Procedures). An actor "is/are responsible|accountable for" doing
    something binds (`_assigns_duty`; 7 of 10 sampled did, and the other 3
    were NIST's suggestions). These do NOT bind, deliberately, and are not a
    gap to fix:

    - **Imperatives, and "Team does X".** A Procedure step is an instruction
      that carries out the Standard requirement above it, which holds the
      citation. Binding them takes Procedures from 2.4% to 65.1% binding and
      adds 2,842 "binds but cites nothing" warnings on correct steps. A
      Standard's 108 imperatives are 73% quoted catalog text. **A modal
      introduces an obligation; an instruction carries one out.** "Never ..."
      and "Do not ..." are imperatives too.
    - **"will"**: 0 of 20 bound; 10 of 13 in Standards were NIST suggesting.
    - **"is/are to (be)"**: 0 of 11; relative clauses.
    - **"is/are expected to"**: 3 of 3 had a document as subject.
    - **"is/are subject to"**: splits between internal rules and legal
      applicability, too fragile for a cue.
    """
    visible = _MARKUP_RE.sub("", sentence)
    found = NONE
    for modality, pattern in _MODALITY_PATTERNS:
        if any(
            not (_states_no_requirement(visible, m) or _epistemic_may(visible, m))
            for m in pattern.finditer(visible)
        ):
            found = modality
            break
    if found not in BINDING and _assigns_duty(visible):
        return OBLIGATION
    return found


#: The predicates #360 added: "X is required." binds like its verb twin "X
#: is required to ...", and "X is not permitted." like "X is prohibited",
#: but only when they state something ABOUT their subject. The context rules
#: below apply to these alone, so every other phrase reads exactly as before.
_PREDICATES = frozenset(
    {"is required", "are required", "is mandatory", "are mandatory"}
    | {"is not permitted", "are not permitted", "is not allowed", "are not allowed"}
)
#: A clause whose subject opens with one of these says nothing is required.
_NEGATIVE_SUBJECTS = frozenset({"no", "nothing", "none", "neither"})
#: A predicate right after one of these sits in a relative or interrogative
#: clause ("what is required", "the evidence that is required"): a noun
#: phrase, not a statement.
_RELATIVE_WORDS = frozenset({"what", "that", "which", "whatever", "who"})
#: A clause opened by one of these is a condition or a reference ("where it
#: is required", "if approval is required"), not a statement that it is.
_CONDITIONAL_WORDS = frozenset(
    {"if", "when", "where", "whenever", "unless", "whether", "as"}
    # A concession or a reason is not the sentence's claim either: "While
    # plans of action are required for federal organizations, other ..."
    # (800-53 PM-4, measured over the shipped catalogs).
    | {"while", "although", "though", "whereas", "because", "since"}
)


#: What closes an aside right before a predicate: a comma, an em or en dash,
#: or `--`.
_ASIDE_CLOSE = re.compile(rf"(,|{chr(0x2014)}|{chr(0x2013)}|--)\s*$")


def _without_asides(prefix: str) -> str:
    """`prefix` without the asides that sit between a subject and its
    predicate (1d on #367): balanced parentheses ("Written approval (see
    Appendix B) is required"), and a comma or dash pair ending right before
    the predicate ("Encryption, where feasible, is required", "USB devices
    -- including phones -- are not allowed"). Without this the clause
    before the predicate was empty and read as "no subject". A CFR
    enumerator after a colon ("information that: (i) Is not permitted")
    still leaves an empty clause, as it should.
    """
    while re.search(r"\([^()]*\)", prefix):
        prefix = re.sub(r"\([^()]*\)", " ", prefix)
    close = _ASIDE_CLOSE.search(prefix)
    if close:
        opening = prefix.rfind(close.group(1), 0, close.start())
        if opening >= 0:
            prefix = prefix[:opening]
    return prefix


#: "may (not)", past these adverbs, then "be" and the word that decides.
_BE_AFTER_MAY = re.compile(
    r"\s+(?:(?:also|only|still|not|always|necessarily|even)\s+)*be\s+(\w+)(\s+as\b)?",
    re.IGNORECASE,
)
#: Passive verbs of judgement or perception, as 80 ruled them on #364.
#: "known" only as "known as" (a name), not "may not be known to anyone",
#: which is a split-knowledge requirement.
_JUDGEMENT = frozenset({"considered", "seen"})
#: Adjectives: what might be true, not what anyone may do (80's ruling (b)
#: on #364). **An explicit list, never a suffix rule**: -able/-al would
#: clear "accountable" and "responsible", and an unlisted word must stay a
#: permission or prohibition, loudly, rather than clear an action. No -ed
#: form is ever cleared: "may not be left unattended" stays a prohibition.
#: **EVALUATIVE adjectives only -- the author guessing -- never DESCRIPTIVE
#: ones, the author allowing** (80 on #375): "Signatures may be electronic"
#: permits a form and stays a permission (1d on #375). This is 80's list on
#: #364, plus "subject" and "able" ("may be subject to", "may not be able
#: to"), which 80 ruled in on #375. Any other word waits for a ruling, and
#: until then stays as it was.
_EPISTEMIC_ADJECTIVES = frozenset(
    {
        "necessary", "possible", "measurable", "effective", "feasible", "practical",
        "appropriate", "available", "applicable", "sufficient", "accurate", "reliable",
        "relevant", "visible", "subject", "able",
    }
)  # fmt: skip


def _epistemic_may(text: str, match: re.Match[str]) -> bool:
    """Whether a "may" / "may not" match states a possibility, not a permission
    or prohibition (#364, 80's rulings): "Systems within these organizations
    may not be considered external.", "(may also be known as accounts of last
    resort)", "links that may be visible to individuals", "Exit interviews
    may not always be possible". *May* binds with an agent and an action
    verb; with a verb of judgement or an adjective it says what might be
    true. "Approval may be required." stays a permission (#360's L4), and
    "may not be done" a prohibition: an action participle is never cleared.

    **Known miss, left by ruling (80, option (c) out):** a thing's capability
    with an active verb still reads as a permission or prohibition: 3
    sentences in the shipped catalogs, measured on #364,
    "Dynamic account creation ... may not support independent verification",
    "Such key combinations ... may not provide a trusted path", "explanations
    may not accurately summarize complex systems".
    """
    if " ".join(match.group(0).lower().split()) not in {"may", "may not"}:
        return False
    after = _BE_AFTER_MAY.match(text[match.end() :])
    if not after:
        return False
    word = after.group(1).lower()
    return (
        word in _JUDGEMENT
        or word in _EPISTEMIC_ADJECTIVES
        or (word == "known" and bool(after.group(2)))
    )


def _states_no_requirement(text: str, match: re.Match[str]) -> bool:
    """Whether a #360 predicate match states nothing about its subject (80's
    ruling): `No action is required.`, `what is required`, `where it is
    required`, and `When organizations are not permitted to delete ...,
    Acme Health shall ...` -- a condition, whose sentence binds by its own
    "shall" (measured on the 33 Standards: without this, that sentence read
    as a prohibition). Only for `_PREDICATES`; any other phrase states what
    it says."""
    if " ".join(match.group(0).lower().split()) not in _PREDICATES:
        return False
    if re.match(r"\s+or\b", text[match.end() :], re.IGNORECASE):
        # A category, not a demand: HIPAA 164.306(d)'s "Implementation
        # specifications are required or addressable." (80's L1 on #360).
        return True
    clause = re.split(rf"[.;:,(){chr(0x2014)}]", _without_asides(text[: match.start()]))[-1]
    words = [w.strip("\"'").lower() for w in clause.split()]
    words = [w for w in words if not re.fullmatch(r"[-*+]|\d+[.)]|\([ivx\d]+\)", w)]
    if not words:
        # No subject in its clause: the predicate continues a clause begun
        # before it -- "information that: (i) Is not permitted by applicable
        # law" (45 CFR 171.204(a)) -- so it states nothing about a subject.
        return True
    # A conditional word anywhere in the clause makes it a condition. This
    # misses "When working remotely MFA is required" (a reduced clause), a
    # named miss: exempting an -ing word after the conditional also caught
    # gerund SUBJECTS, "If testing is required, see the test plan" (1d on
    # #367), which is the direction that invents obligations.
    if words[-1] in _RELATIVE_WORDS or any(w in _CONDITIONAL_WORDS for w in words):
        return True
    # The subject is what follows the last relative word: "... may be such
    # that no explicit terms and conditions are required" (800-53 AC-20).
    last = max((i for i, w in enumerate(words) if w in _RELATIVE_WORDS), default=-1)
    subject = words[last + 1 :]
    return bool(subject) and subject[0] in _NEGATIVE_SUBJECTS


#: Markdown headings are structure, not statements: "### 4.1 Media Protection"
#: commits the organization to nothing, and a heading ending in a numbered
#: section makes the sentence splitter cut in the middle of one. Which lines
#: are headings is decided once, by `_line_kinds` below.


def analyze(text: str) -> list[Statement]:
    """Every sentence in `text`, classified, with the line it starts on.

    Headings are blanked rather than deleted so line numbers still point at
    the document a reader has open.

    A citation trailing its sentence on the following line is attached
    backwards. Generated Standards put it there — the requirement ends with
    a full stop and `[NIST MP-1 ...]` sits underneath — so a naive split
    makes the citation the opening of the *next* sentence, which is usually
    a heading and binds nothing. Left alone that reported every correctly
    cited requirement in the corpus as a weakened one, which is precisely
    the kind of false positive that teaches people to ignore the check.

    **A heading ends a sentence, and nothing is credited across one (#349).**
    Headings used to be blanked and then read straight through, so a
    sentence ending `... [Playbook Map 1.6 Action 8].` ran across `## 3.` and
    `### 3.1` into `- **CAT-01:** ... shall ensure ... [NIST AI RMF Map 2]`,
    and the gate read CAT-01's obligation and citation as NIST's suggestion
    (18 of 2,501 statements across 33 generated Standards). Sentences are
    now split within each run of lines between headings, ATX or setext.
    """
    text, blocks = _heading_blocks(text)

    statements: list[Statement] = []
    for block_offset, block in blocks:
        _analyze_block(text, block_offset, block, statements)
    return statements


#: Line kinds `_line_kinds` returns; the first two are heading TEXT.
_ATX, _SETEXT, _UNDERLINE, _BREAK = "atx", "setext", "underline", "break"
_HEADING_TEXT = (_ATX, _SETEXT)

#: The block parser (#376): CommonMark, as markdown-it reads it. PolicyForge
#: decides nothing here about where a heading, a list item or a paragraph
#: begins or ends; it keeps only what is its own, below and in
#: `_analyze_block`: which blocks are read together, sentence splitting,
#: citation attachment and colon lead-ins.
_MD = MarkdownIt("commonmark")


@dataclass
class _ColonList:
    """A colon lead-in and its list, as line indexes (0-based) (#354)."""

    lead: int
    #: [first, last] line of each item, continuation lines included.
    items: list[list[int]] = field(default_factory=list)
    #: Citation-only lines under the list after a blank line: the list's own.
    trailing: list[int] = field(default_factory=list)

    @property
    def end(self) -> int:
        return max([self.lead, *(last for _, last in self.items), *self.trailing])


@dataclass
class _Structure:
    """One document's block structure, read once from markdown-it's tokens."""

    #: Each line's block role (`_ATX`, `_SETEXT`, `_UNDERLINE`, `_BREAK`), or None.
    kinds: list[str | None]
    #: The runs of lines read together by `analyze`, as [first, last] (0-based).
    blocks: list[list[int]]
    colon_lists: list[_ColonList]
    #: Each ordered list item's number, as (line, start, end) columns (#365).
    markers: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass
class _Leaf:
    """A block of text markdown-it found: a paragraph, or a code, fence or
    HTML block. `analyze` still reads a code block's text as prose, but since
    #376 each is a block of its own (see `_structure`)."""

    first: int
    last: int
    #: The innermost list item holding it, as its opening token's index.
    item: int | None
    #: The outermost list holding it, when that list is not itself inside a
    #: list item: the only lists a colon lead-in introduces (as before #376).
    top_list: int | None
    only_citations: bool
    after_blank: bool


def _only_citation_lines(lines: list[str], first: int, last: int) -> bool:
    return all(_only_citations(line) for line in lines[first : last + 1] if line.strip())


def _prose_tail(lines: list[str], first: int, last: int) -> int:
    """The last line of a block that is not only citations, or -1."""
    for index in range(last, first - 1, -1):
        if lines[index].strip() and not _only_citations(lines[index]):
            return index
    return -1


def _structure(text: str) -> _Structure:
    """Headings, breaks, blocks and colon lists, from markdown-it's tokens.

    **Deliberately unchanged, where this differs from CommonMark** (80's
    ruling on #376: each is a decision, so changing it is one too, not a
    drift):
    - a setext underline of a lone `-` is not an underline (see the note on
      `_line_kinds`): its paragraph stays in sentence analysis;
    - nor is an underline under a table row or a list item's first line
      (ba and 9b on #421): markdown-it would make the table or the item a
      heading and blank it. Where table rows come before a text line that
      sits right on the underline, 74779c4 made that text line a heading;
      it now stays prose too, which keeps its sentence in analysis;
    - the text of code, fenced and HTML blocks is still read as prose,
      although markdown-it knows they are code;
    - a colon lead-in introduces only a list that is not inside a list item.

    **Changed by #376, and absent from the generated corpus, so the
    conservation test cannot pin them** (9b on #421; each has a hand-written
    test in `test_deontic_block_structure.py`):
    - a code, fenced or HTML block is now a block of its own. A fence right
      after a paragraph no longer joins it, so the paragraph's citation is
      not credited to the sentence after the fence;
    - a `#` line inside a fence is code, not a heading or a section start.
      Before, it was both, which was a defect;
    - a heading inside a list item (`- ## x`) is a heading, as CommonMark
      reads it. Before, it was a list line, read as a sentence;
    - `## x` indented four spaces or more is indented code, read as prose,
      as CommonMark reads it. Before, it was a heading (9b on #421).
    """
    lines = text.split("\n")
    kinds: list[str | None] = [None] * len(lines)
    tokens = _MD.parse(text)
    leaves: list[_Leaf] = []
    boundaries: list[int] = []
    stack: list[tuple[str, int]] = []
    markers: list[tuple[int, int, int]] = []
    previous_last = -1
    for index, token in enumerate(tokens):
        if token.nesting == 1:
            stack.append((token.type, index))
        elif token.nesting == -1:
            stack.pop()
        if token.type == "list_item_open" and stack[-2][0] == "ordered_list_open":
            marker = _ORDERED_MARKER_RE.match(lines[token.map[0]])
            if marker and _splits_off(lines[token.map[0]][marker.start(2) :], marker.group(2)):
                markers.append((token.map[0], marker.start(2), marker.end(2)))
        if token.type == "heading_open":
            first, end = token.map
            underline = lines[end - 1].strip() if token.markup in ("=", "-") else ""
            if underline and any(
                _NOT_SETEXT_TEXT_RE.match(lines[i]) for i in range(first, end - 1)
            ):
                # Not a heading here either: a table, or a list item's first
                # line, over `---` or `===` is a setext heading to
                # markdown-it, and blanking it drops every obligation in it
                # (ba and 9b on #421). It stays prose, as at 74779c4; `---`
                # is a break, and any other underline is text.
                under = end - 1
                if _DASH_BREAK_RE.match(lines[under]):
                    leaves.append(_leaf(lines, first, under - 1, stack, previous_last))
                    kinds[under] = _BREAK
                    boundaries.append(under)
                else:
                    leaves.append(_leaf(lines, first, under, stack, previous_last))
                previous_last = under
                continue
            if token.markup == "-" and underline == "-":
                # Not a heading here: CommonMark says it is, and 1d measured
                # markdown-it agreeing (#350); the line above stays prose.
                leaves.append(_leaf(lines, first, end - 1, stack, previous_last))
                previous_last = end - 1
                continue
            if token.markup.startswith("#"):
                kinds[first] = _ATX
            else:
                for line in range(first, end - 1):
                    kinds[line] = _SETEXT
                kinds[end - 1] = _UNDERLINE
            boundaries.append(first)
            previous_last = end - 1
        elif token.type == "hr":
            kinds[token.map[0]] = _BREAK
            boundaries.append(token.map[0])
            previous_last = token.map[0]
        elif token.type in ("paragraph_open", "code_block", "fence", "html_block") and token.map:
            first, end = token.map
            last = end - 1
            while last > first and not lines[last].strip():
                last -= 1
            leaves.append(_leaf(lines, first, last, stack, previous_last))
            previous_last = last
            section = _SECTION_NUMBER_RE.match(lines[first])
            if (
                token.type == "paragraph_open"
                and section
                and _splits_off(lines[first][section.start(2) :], section.group(2))
            ):
                markers.append((first, section.start(2), section.end(2)))

    blocks, colon_lists = _group(lines, leaves, boundaries)
    return _Structure(kinds=kinds, blocks=blocks, colon_lists=colon_lists, markers=markers)


#: An ordered list item's number, after any indentation and blockquote marks.
_ORDERED_MARKER_RE = re.compile(r"^([ \t]*(?:>[ \t]?)*[ \t]*)(\d{1,9}[.)])(?=[ \t]|$)")
#: A section number opening a paragraph (`6.2. The owner ...`). Not a list
#: marker to CommonMark, but the same noise to the splitter (#365; ba on
#: #421 counted 426 of them left over the corpus).
_SECTION_NUMBER_RE = re.compile(r"^([ \t]*(?:>[ \t]?)*[ \t]*)(\d{1,9}(?:\.\d{1,9})+\.)(?=[ \t]|$)")
#: A line that is not setext heading text here: a table row (`_MD` has no
#: tables, so markdown-it reads one as paragraph text) or a line opening a
#: list item. 74779c4's `_LIST_OR_TABLE_RE`, kept (ba and 9b on #421).
_NOT_SETEXT_TEXT_RE = re.compile(r"^[ \t]*(?:>[ \t]?)*[ \t]*(?:[-*+][ \t]|\d{1,9}[.)][ \t]|\|)")
#: Three or more dashes and nothing else: a thematic break when not an
#: underline.
_DASH_BREAK_RE = re.compile(r"^[ \t]{0,3}(?:-[ \t]*){3,}$")


def _splits_off(rest: str, marker: str) -> bool:
    """Whether the sentence splitter would cut `marker` off `rest` as a
    sentence of its own: `1. Open the console` does, `1. item two` and
    `1. **Scope**` do not, and are left exactly as they were."""
    pieces = _sentences(rest)
    return bool(pieces) and pieces[0][1].strip() == marker


def _blanked_lines(text: str, structure: _Structure) -> list[str]:
    """`text`'s lines with headings and ordered list numbers blanked in place.

    **A list number is not a sentence (#365).** The splitter ends a sentence
    at `. ` before a capital, so `1. Open the console` read as `1.` and
    `Open the console`, and a number under a sentence could be glued to its
    end (`... broadcasting. 1.`). Over the 45 Procedures, 4,133 statements
    were bare numbers, each `none`, diluting every per-statement figure.
    Only a number the splitter would cut off is blanked (`_splits_off`), so
    one it never split keeps its text. Blanked, not deleted, so columns and
    line numbers still point at the document a reader has open.
    """
    lines = text.split("\n")
    lines = [" " * len(line) if structure.kinds[i] else line for i, line in enumerate(lines)]
    for line, start, end in structure.markers:
        lines[line] = lines[line][:start] + " " * (end - start) + lines[line][end:]
    return lines


def _leaf(lines, first, last, stack, previous_last) -> _Leaf:
    item = next((index for kind, index in reversed(stack) if kind == "list_item_open"), None)
    top = next(
        (
            index
            for position, (kind, index) in enumerate(stack)
            if kind in ("bullet_list_open", "ordered_list_open")
            and not any(k == "list_item_open" for k, _ in stack[:position])
        ),
        None,
    )
    after_blank = any(not lines[i].strip() for i in range(previous_last + 1, first))
    return _Leaf(first, last, item, top, _only_citation_lines(lines, first, last), after_blank)


def _group(lines, leaves, boundaries) -> tuple[list[list[int]], list[_ColonList]]:
    """Which leaves `analyze` reads together, and each colon list, in order.

    - Each paragraph is its own block: a blank line ends one (#358).
    - Each list item starts a block (#351), and its later paragraphs stay in it.
    - A leaf of only citations never starts a block. It's credited to the
      block above it, blank line or not (#362). With nothing above it since
      the last heading, it goes to the paragraph below, or, when the next
      block is a list item or there's none before the next heading, it's a
      block of its own, as before #376.
    - A colon lead-in and the list after it are one block (#354), with the
      citation-only lines under the list, after a blank line, as the list's
      own. Lists that follow one another with no prose between belong to the
      same lead-in, as they did before #376.
    """
    boundary_lines = sorted(boundaries)
    next_boundary = 0
    blocks: list[list[int]] = []
    colon_lists: list[_ColonList] = []
    current: list[int] | None = None
    current_item: int | None = None
    colon: _ColonList | None = None
    item_of_colon: int | None = None
    pending: list[int] | None = None
    last_prose: _Leaf | None = None

    def flush() -> None:
        if pending is not None:
            blocks.append(list(pending))

    for leaf in leaves:
        # A heading or break between the last leaf and this one ends everything.
        crossed = False
        while next_boundary < len(boundary_lines) and boundary_lines[next_boundary] < leaf.first:
            crossed = True
            next_boundary += 1
        if crossed:
            flush()
            current = current_item = colon = item_of_colon = pending = last_prose = None

        if leaf.top_list is not None and colon is None and last_prose is not None:
            tail = _prose_tail(lines, last_prose.first, last_prose.last)
            if tail >= 0 and _ends_with_colon(lines[tail]):
                colon = _ColonList(lead=tail)
                colon_lists.append(colon)

        if colon is not None and leaf.top_list is not None:
            if leaf.only_citations and leaf.after_blank:
                colon.trailing.extend(
                    i for i in range(leaf.first, leaf.last + 1) if lines[i].strip()
                )
            elif leaf.item is not None and leaf.item != item_of_colon:
                colon.items.append([leaf.first, leaf.last])
                item_of_colon = leaf.item
            elif colon.items:
                colon.items[-1][1] = leaf.last
            current[1] = leaf.last
            continue
        if colon is not None and leaf.only_citations and leaf.after_blank:
            # Under the list, after a blank line: still the list's own (#354).
            colon.trailing.extend(i for i in range(leaf.first, leaf.last + 1) if lines[i].strip())
            current[1] = leaf.last
            continue
        colon = item_of_colon = None

        if leaf.only_citations:
            if current is not None:
                current[1] = leaf.last
            elif pending is None:
                pending = [leaf.first, leaf.last]
            else:
                pending[1] = leaf.last
            continue
        if leaf.item is not None and leaf.item == current_item and current is not None:
            current[1] = leaf.last
        else:
            if pending is not None and leaf.item is not None:
                flush()
                pending = None
            current = [pending[0] if pending is not None else leaf.first, leaf.last]
            blocks.append(current)
            pending = None
            current_item = leaf.item
        # The lead-in is the last prose before a list, with no list between.
        last_prose = leaf if leaf.top_list is None else None
    flush()
    return blocks, colon_lists


def _line_kinds(lines: list[str]) -> list[str | None]:
    """Each line's block role, or None for ordinary text.

    **The one classification of headings**, read by `analyze` (a boundary),
    by `playbook_tagged_headings` and `heading_statements`, and by
    `grounding`'s sections. Since #376 it is markdown-it's, not a regex's.
    **A lone `-` under text is not an underline, by choice, not by
    CommonMark** (1d on #350: markdown-it reads `text\n-` as a setext h2).
    Excluding it keeps the line above in sentence analysis, where every gate
    reads it; including it would blank that line on a single stray
    character, the destructive direction.
    """
    return _structure("\n".join(lines)).kinds


def _heading_blocks(text: str) -> tuple[str, list[tuple[int, str]]]:
    """`text` with every heading line blanked in place (so line numbers hold),
    and the blocks `analyze` reads together as `(offset, block)`: see
    `_group` for which leaves those are."""
    lines = text.split("\n")
    structure = _structure(text)
    blanked = _blanked_lines(text, structure)
    text = "\n".join(blanked)
    starts = [0]
    for line in blanked:
        starts.append(starts[-1] + len(line) + 1)
    # A block runs on to the line before the next block or heading, blank
    # lines included, as it did before #376.
    stops = sorted(
        [first for first, _ in structure.blocks[1:]]
        + [index for index, kind in enumerate(structure.kinds) if kind]
    )
    blocks = []
    for first, last in structure.blocks:
        end = next((stop for stop in stops if stop > last), len(lines))
        blocks.append((starts[first], text[starts[first] : starts[end] - 1]))
    return text, blocks


def _only_citations(line: str) -> bool:
    """Whether `line` carries nothing but citation tags: backticks, emphasis
    and `. ; : |` around them do not make it prose (1d on #362: `[AU-6].`
    and `` `[AU-6]` `` after a blank line were read as prose, so the first
    was lost and the second became a statement of its own). The same
    reading as `generate/playbook_repair._only_citations`. A blank line is
    not a line of citations."""
    return bool(line.strip()) and not _CITATION_RE.sub("", line).strip(" 	`*_.;:|")


def _ends_with_colon(line: str) -> bool:
    """Whether `line`, less its trailing citations and emphasis, ends in `:`."""
    return _CITATION_RE.sub("", line).rstrip(" \t*_`").endswith(":")


def _analyze_block(text: str, block_offset: int, block: str, statements: list[Statement]) -> None:
    """The sentences of one run of lines between headings, appended to
    `statements`. A leading citation is credited backwards only to a
    sentence in this same block."""
    first_in_block = len(statements)
    for piece_start, raw in _sentences(block):
        offset = block_offset + piece_start + (len(raw) - len(raw.lstrip()))
        sentence = raw.strip()
        if not sentence:
            continue

        # Peel citations off the front and credit them to the sentence
        # above. The full stop lands *before* the citation, so a split puts
        # it at the head of whatever follows — which is either a heading
        # (binding nothing) or the next requirement (binding for its own
        # reasons, and wrongly absorbing this citation). Both readings lose
        # the link between a control and the sentence that implements it.
        trailing_citation = False
        peeled: list[str] = []
        while True:
            leading = _CITATION_RE.match(sentence)
            if not leading:
                break
            trailing_citation = True
            peeled.append(leading.group(0))
            offset += leading.end()
            sentence = sentence[leading.end() :]
            stripped = sentence.lstrip(" \t\r\n.;:|")
            offset += len(sentence) - len(stripped)
            sentence = stripped

        carried: tuple[str, ...] = ()
        if trailing_citation and len(statements) > first_in_block:
            previous = statements[-1]
            statements[-1] = Statement(
                line=previous.line,
                text=previous.text,
                modality=previous.modality,
                cited=True,
                citations=previous.citations + tuple(peeled),
            )
        elif trailing_citation:
            # Nothing above it in this block: a citation opening a block
            # belongs to the sentence it opens, never to one across a heading.
            carried = tuple(peeled)

        if not sentence:
            continue

        inline = carried + tuple(_CITATION_RE.findall(sentence))
        statements.append(
            Statement(
                line=text.count("\n", 0, offset) + 1,
                text=" ".join(sentence.split()),
                modality=classify(sentence),
                cited=bool(inline),
                citations=inline,
            )
        )


#: An ATX heading's markup: indentation, the container markers in front of it
#: (blockquote `>`, and a list item's `-`, `*`, `+` or `1.`), the opening `#`s,
#: and an optional closing run of `#`s. The list markers since #376, where a
#: heading inside a list item (`- ## x`) became a heading, as CommonMark reads
#: it, and its text came out as "- ## x" (9b on #421).
_ATX_MARKS_RE = re.compile(r"^[ \t]*(?:(?:>|[-*+]|\d{1,9}[.)])[ \t]*)*#{1,6}[ \t]*|[ \t]+#+[ \t]*$")


#: Every modal phrase `_MODALITY_PATTERNS` matches, lowercase, longest first.
_HEADING_MODALS = "|".join(
    sorted(
        {
            phrase
            for _, pattern in _MODALITY_PATTERNS
            for phrase in pattern.pattern.split("(?:", 1)[1].rsplit(")", 1)[0].split("|")
        },
        key=len,
        reverse=True,
    )
)
_HEADING_MODAL_RE = re.compile(rf"\b(?:{_HEADING_MODALS})\b", re.IGNORECASE)
#: Words that open a clause rather than assert (80's ruling on #357).
_QUESTION_WORDS = frozenset({"what", "how", "why", "when", "which", "who", "whom", "where"})
#: A modal right after these belongs to a relative clause inside a noun
#: phrase: "Controls That Must Be Applied" names a topic (b5, put to 80).
_RELATIVES = frozenset({"that", "which", "who", "whom", "whose"})


def _states_something(heading: str) -> bool:
    """Whether a heading asserts, so its modality counts (80's ruling on #357).

    **Subject + modal + verb, in any case, and passive counts.** "Password
    Must Be Rotated" and "Acme Health must retain ..." assert. These are
    titles: "What Employees Must Do" (opens with a question word),
    "Must-Have Controls" (the modal leads, and is hyphenated into an
    adjective), and "Shall Statements" (the modal leads: the word is named,
    not used). A modal with no subject before it, or no verb after it, is not
    an obligation. A phrase that is complete in itself ("are prohibited")
    may end the heading.
    """
    plain = " ".join(_MARKUP_RE.sub("", _CITATION_RE.sub("", heading)).split())
    words = plain.lower().split()
    if not words or words[0].strip(":,") in _QUESTION_WORDS:
        return False
    for match in _HEADING_MODAL_RE.finditer(plain):
        before = plain[: match.start()].split()
        if not before or before[-1].lower() in _RELATIVES:
            continue
        after = plain[match.end() :]
        if re.match(r"\s+[A-Za-z]", after) or re.fullmatch(r"[.!]?\s*", after):
            return True
    return False


def heading_statements(text: str) -> list[Statement]:
    """Every heading as a statement, from the one classifier, `_line_kinds`.

    **A heading that states an obligation is an obligation** (80's ruling on
    #352). `analyze` blanks headings, so the uncited and strength checks
    never saw one: an obligation that was heading text, ATX or setext, was
    silent. These are the headings, for those checks to read. An ATX
    heading is its line; a setext heading is its whole paragraph, as
    CommonMark reads it, starting on its first line.
    """
    lines = text.split("\n")
    kinds = _line_kinds(lines)
    found: list[Statement] = []
    index = 0
    while index < len(lines):
        kind = kinds[index]
        if kind not in _HEADING_TEXT:
            index += 1
            continue
        start = index
        if kind == _ATX:
            raw = _ATX_MARKS_RE.sub("", lines[index])
            index += 1
        else:
            while index < len(lines) and kinds[index] == _SETEXT:
                index += 1
            raw = " ".join(line.strip() for line in lines[start:index])
        heading = " ".join(raw.split())
        if not heading:
            continue
        citations = tuple(_CITATION_RE.findall(heading))
        found.append(
            Statement(
                line=start + 1,
                text=heading,
                # A title states nothing, whatever words it uses (#357).
                modality=classify(heading) if _states_something(heading) else NONE,
                cited=bool(citations),
                citations=citations,
                heading=True,
            )
        )
    return found


def weakened_citations(text: str) -> list[Statement]:
    """Sentences that cite a control and do not bind, and headings that do.

    **A heading counts only when it states something** (a modality other
    than none): "NIST should ..." set as a heading is a weakened citation,
    but a cited title such as `## Account Management [NIST 800-53 AC-2]`
    states nothing, and reporting every one would make the check noise.
    The choice is b5's on #352, measured on the 33 generated Standards and
    put to 80 on the PR.
    """
    sentences = [s for s in analyze(text) if s.weakens_a_citation]
    headings = [h for h in heading_statements(text) if h.modality != NONE and h.weakens_a_citation]
    return sorted(sentences + headings, key=lambda s: s.line)


def binding_share(text: str) -> tuple[int, int]:
    """(binding sentences, sentences carrying any modality at all).

    The denominator excludes plain declaratives on purpose. A Standard is
    mostly prose — purpose, scope, definitions — and counting that as
    "unbound" would make every document look permissive.
    """
    modal = [s for s in analyze(text) if s.modality != NONE]
    return sum(1 for s in modal if s.binds), len(modal)

"""What a framework source tag looks like, decided once.

A generated document carries its traceability inline, as bracketed tags
after the sentence that implements a control: `[NIST AC-2 | HIPAA
164.308(a)(3)(i)]`. Four places read them — the edit path, to refuse a
revision that dropped one; `check`, to report a tag the synthesis carries
and the document lost; `frameworks/drift`, to find the documents a changed
control reaches; and the deontic analysis, to know which sentences claim to
implement something. Each had its own copy of the pattern, and every copy
was a hand-written list of framework names.

A list is the wrong shape for this. The names in a tag are chosen by the
model at generation time, by example rather than by rule, and the bundled
starter set already carries `[ARC PE-1 ...]` where the list said
`ARC-AMPE`: twenty-two tags that every one of those readers looked straight
through. `check` could not report them dropped, drift could not reach the
documents that cite them, and the counts `satisfies` prints were a floor
that said so in its docstring. Adding `ARC` to four lists fixes the tags
seen today and none of the tags a new catalog, or a model with a different
habit, writes tomorrow.

So a tag is recognised by its shape, not by a roll call:

* it opens with a framework name — one or more capitalised tokens
  (`NIST`, `ARC-AMPE`, `HIPAA Security Rule`);
* the name is followed by a requirement identifier, which is any token
  carrying a digit (`AC-2`, `164.308(a)`, `01.a`, `800-53`);
* it is not a wikilink and not the text of a markdown link.

The identifier rule is what keeps prose out. `[NIST AI RMF alignment]` and
`[NIST CAVP]` occur in this repository's own documents and were matched by
the old list as citations, which they are not; a name with nothing
number-shaped after it is a phrase in brackets. Measured over every text in
the repository before this shipped: the shape matches every tag the list
matched, adds none, and drops exactly those six phrases.

Which catalog a name points at is a separate question, answered by
`topics/satisfies.resolve_framework` against the catalogs actually loaded;
this module only says what is a tag.

One trap, named because it cost two reviewers an hour each. A placeholder
whose label opens with a framework name — `[HIPAA Documentation Review
Frequency]`, sitting mid-sentence beside `[Ticketing System]` and
`[Contract Repository]`, standing in for a value nobody has decided — looks
like a citation to anyone reasoning about its form, and the old list
counted it as one because `HIPAA` was on the list. It is not a citation:
the step it sits in is cited by its own heading, `[HIPAA 164.316(b)(1) |
...]`, and the three scans that published it as the corpus's least
followable citation were counting a placeholder. The identifier rule is
what tells the two apart, and it is the only thing that does — backticks
do not, since a few real tags in generated documents are backticked too.
So the rule is: a name with nothing number-shaped after it is a phrase in
brackets, in generated content as much as in prose, and a reader who wants
it reported as something else should first go and read it in its document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A name token: a capital, letters, hyphenated letters, optionally ending in
#: digits (`NIST`, `ARC-AMPE`, `GovRAMP`, `SOC2`). A hyphen followed by a
#: digit is what an identifier looks like (`PE-1`, `800-53`), so it ends the
#: name rather than extending it, which is what lets `framework_name` stop
#: at `ARC` in `[ARC PE-1 ...]`.
_NAME_WORD = r"[A-Z][A-Za-z]*(?:-[A-Za-z]+)*\d*"
_NAME = rf"{_NAME_WORD}(?: {_NAME_WORD})*"

#: One framework source tag, whole. No capturing group, on purpose: every
#: reader calls `findall` and expects the tag as written, and a group would
#: quietly turn that into the framework name alone.
SOURCE_TAG_RE = re.compile(
    r"(?<!\[)\[(?!\[)"  # an opening bracket, not part of a `[[wikilink]]`
    rf"(?:{_NAME})"  # capitalised name tokens
    r"\s+(?=[^\]\s]*\d)"  # then a token carrying a digit: the requirement id
    r"[^\]]*\]"  # the rest of the tag
    r"(?!\()"  # and not `[text](url)`
)

_LEADING_NAME_RE = re.compile(rf"\[({_NAME})(?:\s|\])")


def source_tags(text: str) -> list[str]:
    """Every source tag in `text`, in order, as written."""
    return SOURCE_TAG_RE.findall(text)


_KNOWN_NAMES: frozenset[str] | None = None


def known_framework_names() -> frozenset[str]:
    """Every framework name a catalog on disk goes by, read once (#340).

    See `frameworks.registry.known_framework_names`. A config file that does
    not parse is named in one warning and the default search paths are read
    instead, so a tag check never becomes a config traceback: the rule 1d set
    for framework keys on #344, through the same `config_or_defaults`.
    """
    global _KNOWN_NAMES
    if _KNOWN_NAMES is None:
        from policyforge.frameworks.registry import config_or_defaults
        from policyforge.frameworks.registry import known_framework_names as read

        config = config_or_defaults("framework names were read")
        _KNOWN_NAMES = read(config)  # assigned only after the read succeeds
    return _KNOWN_NAMES


_KNOWN_IDS: dict[str, set[str]] | None = None


def known_requirement_index() -> dict[str, set[str]]:
    """Every requirement id a catalog on disk holds, by normalized framework,
    read once (#189). See `frameworks.registry.known_requirement_index`; read
    through the same `config_or_defaults` as the names."""
    global _KNOWN_IDS
    if _KNOWN_IDS is None:
        from policyforge.frameworks.registry import config_or_defaults
        from policyforge.frameworks.registry import known_requirement_index as read

        config = config_or_defaults("requirement ids were read")
        _KNOWN_IDS = read(config)  # assigned only after the read succeeds
    return _KNOWN_IDS


def reset_known_framework_names() -> None:
    """Forget the cached names, ids and disk scope: after a catalog is
    written, or in tests. All together, so a tag's name and its id can't be
    read from two different moments."""
    global _KNOWN_NAMES, _KNOWN_IDS, _DISK_SCOPE
    _KNOWN_NAMES = None
    _KNOWN_IDS = None
    _DISK_SCOPE = None


@dataclass(frozen=True)
class TagPart:
    """One `|`-separated citation in a tag, with the framework it names."""

    #: The known framework name the part begins with or inherits; "" if none.
    framework: str
    #: The part after that name, whitespace-normalised: the id and qualifier.
    rest: str
    #: True when `framework` came from an earlier part of the same tag.
    inherited: bool = False

    @property
    def citation(self) -> str:
        """The citation written out in full: `NIST AI RMF Playbook Govern 1.1 Action 2`."""
        return " ".join(p for p in (self.framework, self.rest) if p)

    @property
    def baseline(self) -> bool:
        """A framework named with no id, `ARC-AMPE` in `[NIST IR-1 | ARC-AMPE]`:
        a reference to that framework as a baseline, not a citation of any
        requirement (80's citation form, 5928 on #17). No reader resolves it,
        and none reports it unresolved."""
        return bool(self.framework) and not self.rest


def _names_a_known_framework(word: str, names) -> bool:
    """Whether `word` is one of `names`, or the start of one at a word
    boundary: `NIST` of `NIST 800-53`, `HIPAA` of `HIPAA Security Rule`.
    Ambiguity doesn't matter here; which catalog it names is resolved later,
    by the id (`topics.satisfies.resolve_framework`)."""
    folded = word.casefold()
    for name in names:
        candidate = name.casefold()
        if candidate == folded or (
            candidate.startswith(folded) and not candidate[len(folded)].isalnum()
        ):
            return True
    return False


def _comma_citations(rest: str, names_one=None) -> list[str]:
    """The citations a part lists after one framework name (5928 on #17):
    `IR-1, IR-8` is two. A comma starts a new citation only when the next
    item opens with an id-shaped token (one carrying a digit), so a
    qualifier list stays whole: `IR-4(1) Moderate, High` is one. An item
    opening with `(` continues the citation before it too, as an enhancement
    list does (`CP-9(1), (2), (3)`): read alone, `(2)` names nothing (b5 on
    PR 429). The list is then one citation that resolves to no id, and is
    reported whole, as before #17, never silenced as a qualifier (1d on
    PR 429); expanding it is ranges' work (421). An item opening with a
    framework name, `names_one(word)`, starts a citation of its own: in
    `Map 1, NIST AI RMF Map 1.1` the second is no qualifier of the first."""
    out: list[str] = []
    for segment in rest.split(","):
        segment = segment.strip()
        if not segment:
            continue
        first = segment.split()[0]
        named = names_one is not None and names_one(first)
        if out and not named and (first.startswith("(") or not any(ch.isdigit() for ch in first)):
            out[-1] = f"{out[-1]}, {segment}"
        else:
            out.append(segment)
    return out


def tag_parts(tag: str, frameworks, names_framework=None) -> list[TagPart]:
    """Each citation in a merged tag, a shorthand part given its framework (#333).

    `[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1 Action 2]` is an
    ordinary shorthand, and glm wrote it in 17 of 17 Playbook sentences of
    one Standard. Split on `|` alone, the second part names no framework, so
    the Playbook gate never read those sentences and `satisfies` dropped the
    later parts (b5, #333).

    **80's ruling: inherit, with resolution required, through ONE function.**
    A part that does not begin with one of `frameworks` (matched longest
    first, at a word boundary, case-insensitive) inherits the framework of
    the nearest preceding part in the same tag. A first part with nothing to
    inherit keeps `framework=""`. **Inheriting never makes a citation
    valid**: every caller must resolve the inherited citation against that
    framework's catalog, so `[NIST 800-53 AC-2 | Govern 1.1 Action 2]` gives
    `NIST 800-53 Govern 1.1 Action 2`, which resolves nowhere and is
    reported, not silently read as 800-53.

    `names_framework(word)`, when given, says whether a part's first word is
    a framework the caller can resolve though it is not in `frameworks` --
    an abbreviation such as `ARC` for `ARC-AMPE`, which `satisfies` accepts.
    Such a part names its own framework and does not inherit.

    **80's citation form (5928 on #17)**, which every reader accepts here:

    - a part may list several ids after one name, `NIST IR-1, IR-8`, each its
      own citation of that framework (`_comma_citations`; a later id is
      marked `inherited`, as a shorthand part is);
    - a part that is a framework name alone, `ARC-AMPE` in `[NIST IR-1, IR-8
      | ARC-AMPE]`, is a baseline reference (`TagPart.baseline`), not a
      citation, so it neither inherits nor resolves;
    - a part opening with the start of a known name, `NIST` of `NIST
      800-53`, names its own framework rather than inheriting the part
      before it. Which NIST catalog it names is resolved by its id.

    Both readers of a merged tag's parts call this: the Playbook gate
    (`content/deontic._parts`) and traceability (`topics/satisfies
    .parse_citations`), so both split and inherit by the same rule. **They
    pass different `frameworks`, so they can still attribute a part
    differently** (9b on #337): the gate knows only the Playbook's name, so
    in `[NIST AI RMF Playbook Govern 1.1 Action 1 | NIST AI RMF Govern 1 |
    Govern 1.1]` it reads the third part as the Playbook (it resolves there),
    while `satisfies` reads it as the AI RMF Core, the part it follows. No
    gate verdict depends on that today: such a tag is never Playbook-only,
    because it names a non-Playbook framework explicitly.
    """
    names = sorted({str(f) for f in frameworks if str(f).strip()}, key=len, reverse=True)

    def names_one(word: str) -> bool:
        return _names_a_known_framework(word, names) or (
            names_framework is not None and names_framework(word)
        )

    parts: list[TagPart] = []
    current = ""

    def attribute(text: str) -> tuple[str, str, bool]:
        """(framework, the rest, inherited) for one `|`-separated part."""
        for name in names:
            if text.casefold() == name.casefold():
                # A known name alone, multi-word included (`HIPAA Security
                # Rule`), which the abbreviation branch below would split
                # into a name and an id: a baseline reference (5928 on #17).
                return name, "", False
            if (
                text.casefold().startswith(name.casefold())
                and text[len(name) : len(name) + 1] == " "
            ):
                return name, text[len(name) + 1 :], False
        first, _, rest = text.partition(" ")
        if not rest and names_one(text):
            return text, "", False  # a one-word name or abbreviation alone
        if rest and names_one(first):
            # A name the caller can resolve, or the start of a known one
            # (`ARC` for ARC-AMPE, `NIST` for NIST 800-53): it names a
            # framework, so it neither inherits nor is inherited past. Kept
            # as written; resolving it is the caller's job.
            return first, rest, False
        return current, text, bool(current)

    for raw in tag.strip("[]").replace("\\", "").split("|"):
        text = " ".join(raw.split())
        if not text:
            continue
        framework, rest, inherited = attribute(text)
        if not rest:
            parts.append(TagPart(framework=framework, rest=""))
        citations = _comma_citations(rest, names_one)
        word = rest.split()[0] if rest else ""
        if not framework and len(citations) > 1 and not any(ch.isdigit() for ch in word):
            # A part naming no known framework (`HITRUST` with no HITRUST
            # catalog anywhere): its later ids take the part's first word, so
            # `11.c` is reported as `HITRUST 11.c`, not dropped (1d on PR 429).
            citations = [citations[0], *(f"{word} {c}" for c in citations[1:])]
        for index, citation in enumerate(citations):
            if index and names_one(citation.split()[0]):
                # A later item naming its own framework is attributed as a
                # part of its own would be.
                named, named_rest, _ = attribute(citation)
                parts.append(TagPart(framework=named, rest=named_rest))
                continue
            parts.append(
                TagPart(framework=framework, rest=citation, inherited=inherited or index > 0)
            )
        current = framework
    return parts


_DISK_SCOPE = None


def disk_scope():
    """Every catalog on disk, as a `topics.satisfies.CatalogScope` built by
    the same `catalog_scope` a run's set is: what a bracket is resolved
    against when no run's catalog set is known (#215). Read once."""
    global _DISK_SCOPE
    if _DISK_SCOPE is None:
        from policyforge.frameworks.registry import config_or_defaults, controls_on_disk
        from policyforge.topics.satisfies import catalog_scope

        config = config_or_defaults("catalogs were read")
        # Assigned only after the read succeeds.
        _DISK_SCOPE = catalog_scope(controls_on_disk(config), "every catalog on disk")
    return _DISK_SCOPE


def is_citation_tag(span: str, scope=None) -> bool:
    """Whether a bracketed span is a citation that resolves, rather than a
    placeholder such as an unfilled role (#189).

    **80's ruling (3512 on #214): a span is a citation only when it names a
    framework a catalog in scope declares, followed by a requirement id that
    exists in that catalog**, by the citation checker's own steps
    (`topics.satisfies`): `parse_citations` splits it, and
    `CatalogScope.cites` decides. **`scope` is the run's catalog set where
    the run has one** (`topics.satisfies.catalog_scope`, the scope
    `satisfies --controls` builds; 80's ruling on #215), so the two readers
    resolve a tag alike by construction. With no scope given it is every
    catalog on disk (`disk_scope`), which can differ from a run's set on an
    abbreviation (below). Anything else is a placeholder, and is reported:

    - a role, whatever its case: `[CISO]`, `[IT]`, `[Security Lead]` (a rule
      wanting a lower-case letter lost the all-caps ones; 1d on #178);
    - a role that names a framework and carries a digit, `[FedRAMP 3PAO]` or
      `[NIST 800-53 Assessor]`, because `3PAO` and `Assessor` are no ids
      (9b on #214; dropping them silently was the dangerous direction);
    - a tag-like span whose id doesn't exist, `[NIST SP 800-53 ZZ-99]`;
    - a bare framework name, `[NIST]`: an incomplete citation, flagged on
      purpose (80, 3512);
    - a framework with no catalog on disk (`[ISO 27001 A.5.1]`, or
      `[SOC 2 CC6.1]` before the user's own catalog exists);
    - a short-form tag whose abbreviation is ambiguous IN SCOPE: `NIST`
      resolves only when 800-53 is the ONLY NIST catalog in scope, and the
      bundled set has five (800-53, 800-171, CSF 2.0, the AI RMF and its
      Playbook), so against every catalog on disk `NIST` names several
      (b5 measured it on #214: dropping any one of the other four leaves it
      ambiguous) and `[NIST SC-8]` is reported. Given the run's set with
      800-53 as its only NIST catalog, it resolves, as `satisfies --controls`
      resolves it (9b measured 28 such labels in the 128-document deontic
      corpus on #214; with 800-53's scope they are 0). The generator usually
      writes `NIST 800-53 SC-8`, which resolves either way;
    - a citation of a catalog outside the run's set: `[HIPAA 164.312(a)(1)]`
      for a run drawn from 800-53 alone, as `satisfies` reports it unknown.

    A merged tag counts when any of its parts resolves.
    """
    if not SOURCE_TAG_RE.fullmatch(span):
        return False
    from policyforge.topics.satisfies import parse_citations

    if scope is None:
        scope = disk_scope()
    for framework, requirement_id, _, _ in parse_citations(span, scope.names, scope.index, scope):
        if scope.cites(framework, requirement_id):
            return True
    return False


def names_a_framework(name: str, scope=None) -> bool:
    """Whether `name`, alone, is what a catalog goes by: a framework name a
    catalog on disk (or in `scope`) declares, or the start of one at a word
    boundary (`NIST` for `NIST 800-53`, `HIPAA` for `HIPAA Security Rule`,
    `ARC` for `ARC-AMPE`), the boundary `topics.satisfies.resolve_framework`
    uses. Ambiguity doesn't matter here: `NIST` names several catalogs and is
    still a framework, not a role.

    A framework with no catalog anywhere on disk can't be recognised alone
    (`[ISO]`), so it reads as a role; written with an id (`[ISO 27001
    A.5.1]`) it is citation-shaped and `is_unresolved_citation` catches it.
    """
    folded = " ".join(name.split()).casefold()
    if not folded:
        return False
    names = set(known_framework_names()) | set(getattr(scope, "names", ()) or ())
    for known in names:
        candidate = " ".join(str(known).split()).casefold()
        if candidate == folded:
            return True
        if candidate.startswith(folded) and not candidate[len(folded)].isalnum():
            return True
    return False


def is_unresolved_citation(span: str, scope=None) -> bool:
    """Whether a bracket is written as a framework citation that doesn't
    resolve in `scope`, rather than a placeholder to fill (#222).

    `generate` lists what a run leaves in brackets. A role is filled under
    `org.vendors` or `org.teams`; a citation isn't, and telling the user to
    add `[ISO 27001 A.5.1]` as a vendor is wrong advice. Both halves are the
    citation checker's own (80's ruling on #222: one instrument with #214
    and #221):

    - **whether it resolves** is `is_citation_tag(span, scope)`, the run's
      `CatalogScope`. A bracket that resolves is a citation and never lands
      in either list.
    - **whether it is written as one** is `SOURCE_TAG_RE`, the shape
      `is_citation_tag` and `satisfies` read a citation by: capitalised name
      tokens, then a token with a digit (`[ISO 27001 A.5.1]`, `[NIST
      ZZ-99]`, `[NIST AI RMF Govern 1.1]` outside the run's set). `satisfies`
      reports every such bracket that doesn't resolve as an unknown citation,
      so this list and its report agree. **That includes a role written in
      that shape**, such as `[FedRAMP 3PAO]`, `[NIST 800-53 Assessor]` or
      `[Tier 2 Support]` (80's table, 3462 on #214): no shape rule can tell
      them from `[ISO 27001 A.5.1]` without a vocabulary of every framework.
      Measured on the 128-document corpus: 0 such labels are left with each
      document's own catalog set.
    - **or a framework named alone**, `[NIST]` or `[FedRAMP]`: an incomplete
      citation, flagged on purpose (80's ruling 3512 on #214), by
      `names_a_framework`.
    """
    if is_citation_tag(span, scope):
        return False
    if SOURCE_TAG_RE.fullmatch(span):
        return True
    inner = span.strip()
    return inner.startswith("[") and inner.endswith("]") and names_a_framework(inner[1:-1], scope)


def framework_name(tag: str) -> str:
    """The framework a tag opens with, as written: `ARC` in `[ARC PE-1 | ...]`.

    As written, not resolved: which catalog `ARC` names is
    `topics/satisfies.resolve_framework`'s question.
    """
    match = _LEADING_NAME_RE.match(tag)
    return match.group(1) if match else ""

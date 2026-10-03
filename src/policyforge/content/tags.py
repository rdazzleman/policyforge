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


def reset_known_framework_names() -> None:
    """Forget the cached names: after a catalog is written, or in tests."""
    global _KNOWN_NAMES
    _KNOWN_NAMES = None


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
        return f"{self.framework} {self.rest}" if self.framework else self.rest


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
    parts: list[TagPart] = []
    current = ""
    for raw in tag.strip("[]").replace("\\", "").split("|"):
        text = " ".join(raw.split())
        if not text:
            continue
        for name in names:
            if (
                text.casefold().startswith(name.casefold())
                and text[len(name) : len(name) + 1] == " "
            ):
                parts.append(TagPart(framework=name, rest=text[len(name) + 1 :]))
                current = name
                break
        else:
            first, _, rest = text.partition(" ")
            if rest and names_framework is not None and names_framework(first):
                # An abbreviation the caller can resolve (`ARC` for ARC-AMPE):
                # it names a framework, so it neither inherits nor is inherited
                # past. Kept as written; resolving it is the caller's job.
                parts.append(TagPart(framework=first, rest=rest))
                current = first
            else:
                parts.append(TagPart(framework=current, rest=text, inherited=bool(current)))
    return parts


def framework_name(tag: str) -> str:
    """The framework a tag opens with, as written: `ARC` in `[ARC PE-1 | ...]`.

    As written, not resolved: which catalog `ARC` names is
    `topics/satisfies.resolve_framework`'s question.
    """
    match = _LEADING_NAME_RE.match(tag)
    return match.group(1) if match else ""

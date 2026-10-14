"""Which ids a topic may anchor to own a requirement: one rule for every caller (#377).

"Which control does this id belong to" (`AC-2(3)` -> `AC-2`, `Govern 1.1` ->
`Govern 1`) was written separately at least seven times: coverage, bundles,
satisfies, drift, discover, the programme's parameter scope, and drift's
own `_topic_keys`. Each copy was found broken on its own (#312, #318, #335,
#339, #345, #369), and the commonest break was the same one: **a rule that
reads no framework**. `Govern 1.1` is an AI RMF subcategory under the
category `Govern 1`, and it is also the id of the Playbook's suggestions for
that subcategory, which no topic owns. A rule given only the id cannot tell
them apart, so it either misses the Core (discover, #345) or claims the
Playbook (programme, #336).

So the rule takes the framework, and the grammar is chosen by it:

    anchor_keys("AC-2(3)", "NIST 800-53")              {"AC-2(3)", "AC-2"}
    anchor_keys("Govern 1.1", "NIST AI RMF")           {"Govern 1.1", "Govern 1"}
    anchor_keys("Govern 1.1", "NIST AI RMF Playbook")  set()

A topic anchoring the parent owns the child, and a topic anchoring the child
owns it directly; which of the two wins is the caller's question (coverage
and bundles prefer the direct claim).

**Two breadths, named, because they answer different questions** (80's
ruling on #377):

- `anchor_keys` is **topic reach**: which team owns this. Only the catalogs
  a topic may anchor, and only the id and the control it hangs off.
- `family_of` is **document reach**: which documents to re-read when this
  changes. Deliberately broader, because missing a document is the
  dangerous direction: the **whole family** within the change's own
  framework, for the catalogs that have a family grammar. `AC-2(1)`
  reaches a document citing its sibling `AC-2(3)`; `Govern 1.3` reaches one
  citing `Govern 1` or any `Govern 1.x`; a Playbook action reaches Playbook
  citations of the same subcategory and never the Core's.

  **The regulatory catalogs have no grammar here; they declare a unit**
  (#423, 80's ruling): HIPAA, 42 CFR Part 2, Information Blocking and
  ONC 170.315 nest their ids several levels deep, and their catalogs
  mostly omit the parents (`164.308(a)(1)` is cited, `164.308(a)(1)(i)` is
  the id). Each `framework.yaml` names the unit the regulation calls a
  requirement (`family:`), and `regulatory_families` below resolves a
  citation to it, a paragraph the catalog does not carry included.

Ingest code that builds a catalog's own hierarchy (`ingest/ai_rmf.py`)
reads the source, not a topic registry or a document, and stays its own.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress

from policyforge.mapping.crosswalk import NIST_ANCHOR, TOPIC_ANCHORS, normalize_framework

#: How a child id names its parent, per catalog a topic may anchor. Keyed by
#: normalized framework, and **held equal to `TOPIC_ANCHORS`** by
#: `tests/test_anchoring.py`.
#:
#: **A grammar this does not know silently claims nothing** (moved here from
#: `coverage.py`, where it was learned). A topic anchoring `Govern 1` used to
#: report seven orphans: no error, a plausible number, and a topic author
#: would have "fixed" it by anchoring all seven subcategories, arriving at a
#: correct-looking registry built around a defect. The workaround looks like
#: diligence, which is what makes the silence expensive. Hence the test that
#: a catalog made anchorable cannot be missing from this table.
_PARENT_RES: dict[str, re.Pattern[str]] = {
    # AC-2(1) -> AC-2
    "nist-800-53": re.compile(r"^([A-Za-z]{2}-\d+)\(\d+\)$"),
    # Govern 1.1 -> Govern 1
    "nist-ai-rmf": re.compile(r"^([A-Za-z]+ \d+)\.\d+$"),
    # 164.502(a)(5)(iii) -> 164.502: the Privacy and Breach Notification Rules
    # are one control per section, every paragraph an enhancement of it (#420).
    "hipaa-privacy": re.compile(r"^(\d+\.\d+)\(.+\)$"),
    "hipaa-breach": re.compile(r"^(\d+\.\d+)\(.+\)$"),
}


def parent_of(requirement_id: str, framework: str) -> str | None:
    """The control `requirement_id` hangs off in `framework`, or None.

    None for a top-level control, and for any framework a topic cannot
    anchor: the Playbook's `Govern 1.1` has no parent a topic could own.
    """
    rule = _PARENT_RES.get(normalize_framework(framework))
    match = rule.match(requirement_id) if rule else None
    return match.group(1) if match else None


def anchor_keys(requirement_id: str, framework: str) -> frozenset[str]:
    """The ids a topic may anchor to own `requirement_id` from `framework`.

    The id itself and its parent, for a catalog topics anchor; nothing for
    any other catalog, whose requirements a topic reaches only through the
    crosswalk to 800-53 (`ANCHOR_DECISIONS`). Ids are returned as written.
    A topic's anchors arrive in their catalog's spelling (`canonical_anchor`,
    applied where anchors are read in, #188), so callers compare them
    verbatim.
    """
    if normalize_framework(framework) not in TOPIC_ANCHORS:
        return frozenset()
    parent = parent_of(requirement_id, framework)
    return frozenset({requirement_id, parent} if parent else {requirement_id})


#: How each catalog topics anchor spells its ids, case set aside (#188). An
#: anchor with one of these shapes, in any case, is rewritten to that
#: catalog's own spelling where anchors are read in (the topic registry, and
#: `synthesize --nist-controls`); anything else is kept exactly as written.
#: So every reader compares one spelling, and none of them needs its own case
#: rule: five readers once disagreed, three verbatim and two upper-casing, so
#: `ac-3` was unknown to coverage, bundle and addresses and owned in drift
#: and parameters.
#:
#: Measured when this was written: all 1,014 ids of 800-53 r5 and all 91 of
#: the AI RMF already have these forms, so the rewrite changes nothing a
#: catalog spells, and no id in any other bundled catalog is changed by it.
#: HIPAA's `164.308(a)(1)` and CSF's `GV.OC-01` don't fit, so their case
#: stands. Zero-padding isn't case: `ac-03` becomes `AC-03`, an id 800-53
#: doesn't have (it never pads), so coverage reports it as unknown.
_ANCHOR_FORMS: tuple[tuple[re.Pattern[str], Callable[[str], str]], ...] = (
    # AC-2, AC-2(3)
    (re.compile(r"[A-Za-z]{2}-\d+(?:\(\d+\))?"), str.upper),
    # Govern 1, Govern 1.1, and Map, Measure, Manage the same way
    (re.compile(r"(?:govern|map|measure|manage) \d+(?:\.\d+)?", re.IGNORECASE), str.capitalize),
)


def canonical_anchor(written: str) -> str:
    """`written` in its catalog's own spelling when it has the whole shape
    of an id a topic can anchor, in any case; otherwise exactly as written
    (#188).

        canonical_anchor("ac-2(3)")        "AC-2(3)"
        canonical_anchor("GOVERN 1.1")     "Govern 1.1"
        canonical_anchor("164.308(a)(1)")  "164.308(a)(1)"
    """
    for form, spell in _ANCHOR_FORMS:
        if form.fullmatch(written):
            return spell(written)
    return written


#: The family grammar for document reach, per catalog, each by its own shape.
#: The regulatory catalogs are not here: they declare their unit in their
#: manifest (`catalog_families`, #423). 800-53's is shared by the catalogs that write 800-53 ids
#: (FedRAMP, ARC-AMPE). The Playbook nests its actions under the subcategory
#: they serve, and stops there: its family is never the Core's category.
_FAMILY_RES: dict[str, re.Pattern[str]] = {
    **_PARENT_RES,
    "fedramp": _PARENT_RES["nist-800-53"],
    "arc-ampe": _PARENT_RES["nist-800-53"],
    # Govern 1.1 Action 3 -> Govern 1.1
    "nist-ai-rmf-playbook": re.compile(r"^([A-Za-z]+ \d+\.\d+) Action \d+$"),
}


def family_of(requirement_id: str, framework: str) -> str:
    """The family `requirement_id` belongs to in `framework`, for document
    reach: the control it hangs off, or itself. Two ids in one framework with
    the same family are re-read together."""
    rule = _FAMILY_RES.get(normalize_framework(framework))
    match = rule.match(requirement_id) if rule else None
    return match.group(1) if match else requirement_id


def topic_keys(
    requirement_id: str,
    framework: str,
    crosswalk: dict[str, dict[str, list[str]]] | None = None,
    relationships: dict[tuple[str, str, str], str] | None = None,
) -> frozenset[str]:
    """The ids a topic may anchor to be reached by `requirement_id`, from ANY
    catalog: topic reach, whole (80's ruling (B) on #377).

    For a catalog topics anchor, `anchor_keys`. For any other, **through the
    crosswalk**, as `ANCHOR_DECISIONS` says those catalogs are reached: the
    anchor keys of every 800-53 control the crosswalk maps the requirement
    to, in full by `coverage.reaches_in_full` (the one reading of "partial",
    #185). `crosswalk` is `build_crosswalk` over the catalogs actually
    loaded, keyed NIST-first, so a catalog nobody loaded reaches nothing.

    A FedRAMP or ARC-AMPE id reaches what it did when this matched by id
    shape, because their crosswalks map each id to itself. HIPAA and
    800-171 reach topics for the first time, through NIST's and HHS's own
    mappings. A catalog with no crosswalk (the Playbook, Part 2, ONC,
    Information Blocking) reaches none.
    """
    if normalize_framework(framework) in TOPIC_ANCHORS:
        return anchor_keys(requirement_id, framework)
    keys: set[str] = set()
    for nist_id in crosswalked(requirement_id, framework, crosswalk, relationships):
        keys |= anchor_keys(nist_id, NIST_ANCHOR)
    return frozenset(keys)


def crosswalked(
    requirement_id: str,
    framework: str,
    crosswalk: dict[str, dict[str, list[str]]] | None,
    relationships: dict[tuple[str, str, str], str] | None = None,
) -> set[str]:
    """The 800-53 ids the loaded crosswalk maps `requirement_id` to in full.

    Empty for 800-53 itself and for any catalog with no mapping loaded.
    """
    return set(
        reverse_index(crosswalk, relationships).get(
            (normalize_framework(framework), requirement_id), ()
        )
    )


def reverse_index(
    crosswalk: dict[str, dict[str, list[str]]] | None,
    relationships: dict[tuple[str, str, str], str] | None = None,
) -> dict[tuple[str, str], frozenset[str]]:
    """`{(catalog key, requirement id): the 800-53 ids it maps to in full}`.

    The crosswalk is keyed NIST-first; every reach from another catalog asks
    it the other way round, so this is built once per crosswalk rather than
    scanned once per id.
    """
    from policyforge.topics.coverage import reaches_in_full

    index: dict[tuple[str, str], set[str]] = {}
    for nist_id, mapped in (crosswalk or {}).items():
        for key, requirement_ids in mapped.items():
            for requirement_id in requirement_ids:
                if reaches_in_full(key, requirement_id, nist_id, relationships or {}):
                    index.setdefault((key, requirement_id), set()).add(nist_id)
    return {pair: frozenset(ids) for pair, ids in index.items()}


def hubs(
    requirement_id: str,
    framework: str,
    reverse: dict[tuple[str, str], frozenset[str]],
) -> set[str]:
    """The 800-53 families a requirement stands for (80's hub rule on #377):
    its own family if it is 800-53, otherwise the families of what the
    loaded crosswalk maps it to in full. Two citations with a hub in common
    are about the same control, whichever catalog each names."""
    key = normalize_framework(framework)
    if key == NIST_ANCHOR:
        return {family_of(requirement_id, NIST_ANCHOR)}
    return {family_of(nist_id, NIST_ANCHOR) for nist_id in reverse.get((key, requirement_id), ())}


#: An 800-53-shaped id anywhere in a text. Recognition, for drift's fallback
#: on a citation that does not resolve (80's ruling on #418); the family is
#: still `family_of`'s.
_SHAPE_RE = re.compile(r"\b([A-Z]{2}-\d+(?:\(\d+\))?)")

#: A trailing statement part: `(a)` in `AC-6(1)(a)`, `(A)` in `164.308(a)(1)(ii)(A)`.
_LAST_PART_RE = re.compile(r"\([^()]*\)$")


def shaped_ids(text: str) -> list[str]:
    """Every 800-53-shaped id in `text`, in order."""
    return _SHAPE_RE.findall(text)


def is_800_53_shaped(requirement_id: str) -> bool:
    return bool(_SHAPE_RE.fullmatch(requirement_id))


def enclosing(requirement_id: str, has) -> str:
    """`requirement_id` with statement parts taken off, last first, until
    `has(id)` is true; itself if no prefix is. `AC-6(1)(a)` -> `AC-6(1)`."""
    current = requirement_id
    while not has(current):
        match = _LAST_PART_RE.search(current)
        if not match:
            return requirement_id
        current = current[: match.start()]
    return current


# ---- the regulatory catalogs (80's ruling on #423) ----

#: A section: the id before its first paragraph, `171.202(b)` -> `171.202`.
_SECTION_RE = re.compile(r"^[^(]+")
#: A criterion: a section and two paragraph levels, `170.315(b)(1)(iii)` ->
#: `170.315(b)(1)`.
_CRITERION_RE = re.compile(r"^[^(]+\([^()]*\)\([^()]*\)")
#: A top-level entry the ETL titled as its standard's specifications.
_SPEC_TITLE_RE = re.compile(r"^Implementation specifications?\b", re.IGNORECASE)


_PAREN_RE = re.compile(r"\(([^()]+)\)")
_ROMAN_VALUES = (("x", 10), ("ix", 9), ("v", 5), ("iv", 4), ("i", 1))


def _roman(value: str) -> int | None:
    """A lower-case roman numeral's value, or None; written as `_to_roman`
    writes it, so `iiii` is no numeral."""
    total, at = 0, 0
    for numeral, worth in _ROMAN_VALUES:
        while value.startswith(numeral, at):
            total += worth
            at += len(numeral)
    return total if total and at == len(value) and _to_roman(total) == value else None


def _to_roman(number: int) -> str:
    out = ""
    for numeral, worth in _ROMAN_VALUES:
        while number >= worth:
            out += numeral
            number -= worth
    return out


def _sequences(first: str, last: str, limit: int) -> list[list[str]]:
    """Each reading of `first`..`last` as a run, both ends of one kind:
    numbers, lower-case letters, upper-case letters (HIPAA's `(ii)(A)-(C)`,
    80's 6192 on PR 437), lower-case roman numerals. `(i)-(v)` reads both as
    letters and as roman numerals; the catalog decides which
    (`satisfies.citation_members`).

    A run of more than `limit` members has no reading, and is never built
    (b5 on PR 437: `(1)-(10000000)` took 102 s and 1.2 GB to be refused).
    The caller's `limit` is how many ids the catalog holds, which bounds
    how many members it can carry."""
    runs: list[list[str]] = []

    def run(start: int, stop: int, spell) -> None:
        if start <= stop and stop - start < limit:
            runs.append([spell(n) for n in range(start, stop + 1)])

    if first.isascii() and last.isascii() and first.isdigit() and last.isdigit():
        # More digits than `int` reads (4,300) is no catalog's id: no reading.
        with suppress(ValueError):
            run(int(first), int(last), str)
    letters = len(first) == len(last) == 1 and first.isascii() and last.isascii()
    for same_case in (str.islower, str.isupper):
        if letters and same_case(first) and same_case(last):
            run(ord(first), ord(last), chr)
    a, b = _roman(first), _roman(last)
    if a is not None and b is not None:
        run(a, b, _to_roman)
    return runs


def _parts(text: str) -> tuple[str, list[str]] | None:
    """`164.316(b)(1)` -> ("164.316", ["b", "1"]); None if the text is more
    than a base and parenthesised parts."""
    base = text.split("(", 1)[0]
    parts = _PAREN_RE.findall(text[len(base) :])
    return (base, parts) if base + "".join(f"({p})" for p in parts) == text else None


def _range_readings(token: str, limit: int) -> list[list[str]]:
    """The member lists a SIMPLE range can mean (80's ruling on #421): two
    endpoints identical except for their last parenthesised part, written
    `164.316(b)(1)-(3)` or `164.316(b)(1)-(b)(3)`. Anything else, a
    cross-level `164.310(d)(1)-(d)(2)(iv)` included, has no reading."""
    left, dash, right = token.partition(")-(")
    if not dash:
        return []
    left_parts = _parts(left + ")")
    right_parts = _parts("(" + right)
    if left_parts is None or right_parts is None or right_parts[0]:
        return []
    base, lefts = left_parts
    rights = right_parts[1]
    if not lefts or len(rights) > len(lefts) or lefts[-len(rights) : -1] != rights[:-1]:
        return []
    stem = base + "".join(f"({p})" for p in lefts[:-1])
    return [[f"{stem}({v})" for v in run] for run in _sequences(lefts[-1], rights[-1], limit)]


def id_stem(requirement_id: str) -> str | None:
    """`requirement_id` without its last parenthesised part: `CP-9` of
    `CP-9(1)`, `164.316(b)` of `164.316(b)(1)`; None when it has none. What
    a bare `(2)` after a comma takes its stem from (#421)."""
    parsed = _parts(requirement_id)
    if parsed is None or not parsed[1]:
        return None
    base, parts = parsed
    return base + "".join(f"({p})" for p in parts[:-1])


def member_readings(token: str, limit: int) -> list[list[str]]:
    """The member lists one citation token can mean (#421): a simple range's
    readings (`_range_readings`), or the token itself when it is one id.
    Empty when it is neither: a range that isn't simple, or text that isn't
    a base and parenthesised parts. A range reads as at most `limit`
    members (`_sequences`)."""
    if ")-(" in token:
        return _range_readings(token, limit)
    return [[token]] if _parts(token) is not None else []


#: A range's separator between two parenthesised parts, as written: a hyphen,
#: an en dash or `through`, spaced or not (80's ruling 6185 on PR 437).
_SEPARATOR_RE = re.compile(
    r"\)\s*(?:-|\u2013|\u2014|\bto\b|\bthrough\b|\bthru\b)\s*\(", re.IGNORECASE
)
#: A list's joiner between two parenthesised parts: `(1) and (3)`,
#: `(1) & (2)`, `(1), and (3)` (80's 6197 on PR 437).
_JOINER_RE = re.compile(r"\)\s*(?:,\s*)?(?:\band\b|&)\s*\(", re.IGNORECASE)
#: A member-shaped part: a number, a letter or a roman numeral in brackets,
#: `(3)`, `(B)`, `(iii)`; not a word, as `(backups)` is (80's 6197 on PR 437).
_MEMBER_TOKEN_RE = re.compile(r"\((?:\d+|[A-Za-z]|[ivx]+|[IVX]+)\)")


def carries(ids, requirement_id: str) -> bool:
    """Whether a catalog holding `ids` carries `requirement_id` or an id
    beneath it (`164.316(b)(2)` over `164.316(b)(2)(i)`). What a range's
    member needs (80's 6189 on PR 437): a citation of a statement part below
    a carried id resolves too, but a range must not invent members that way."""
    return requirement_id in ids or any(i.startswith(requirement_id + "(") for i in ids)


def stands_below(ids, requirement_id: str, text: str | None = None) -> bool:
    """Whether a catalog holding `ids` lets a citation of `requirement_id`
    stand for what encloses it (80's 6235 on #438): the catalog carries it
    or an id beneath it (`carries`), or it is a part below a carried LEAF,
    an id the catalog doesn't subdivide. Below an id the catalog does
    subdivide, its own list says which parts exist, so an unlisted one
    (`164.502(a)(1)(40)` below `164.502(a)(1)`) is invented: no evidence.
    **Except its first item** (`(1)`, `(a)`, `(i)` or `(A)`) when the list
    it heads is carried from the second on: eCFR folds `164.502(b)(1)`,
    the minimum-necessary standard, into `164.502(b)`'s own paragraph,
    while `(b)(2)` is an id (1d on PR 455). Numbering starts at the first,
    so it exists; its text is the parent's, and a part below it is read
    there as below a leaf.

    **Below a leaf**, `text` is the leaf's own text, given for a catalog
    whose manifest says `leaf_parts: listed` (#441): the part stands only
    when that text lists it (`lists_part`), so `170.315(a)(1)(99)` is
    reported and `170.315(b)(1)(iii)` resolves. With no `text`, the
    catalog keeps the residual: any part below a leaf stands, invented or
    not. Which catalogs keep it, and the measurement behind each setting,
    are in their manifests; `satisfies` names them in its run."""
    if carries(ids, requirement_id):
        return True
    parent = enclosing(requirement_id, lambda i: i in ids)
    if parent not in ids:
        return False
    rest = requirement_id[len(parent) :]
    if any(i.startswith(parent + "(") for i in ids):
        first = _folded_first_item(ids, parent, rest)
        if first is None:
            return False
        parent, rest = parent + first, rest[len(first) :]
        if not rest:
            return True
    if text is None:
        return True
    return lists_part(parent, text, rest)


#: Each list's first item, and the second item that shows the list exists:
#: what numbering starts at, read from what follows it. By the item itself,
#: not by kind: `(i)` is both a roman numeral and a letter, and a match by
#: kind read `164.502(a)(1)(a)` and `164.404(i)` as first items (b5 on
#: PR 455).
_SECOND_ITEM = {"1": "2", "a": "b", "i": "ii", "A": "B"}


def _folded_first_item(ids, parent: str, part: str) -> str | None:
    """`(1)` when `part` opens with the first item of a list `parent`
    carries from its second item on (`(1)` with `(2)` carried, `(a)` with
    `(b)`, `(i)` with `(ii)`, `(A)` with `(B)`), its own text being folded
    into `parent`'s by the source; None otherwise."""
    enumerators = _PAREN_RE.findall(part)
    if not enumerators or enumerators[0] not in _SECOND_ITEM:
        return None
    first = f"({enumerators[0]})"
    second = f"{parent}({_SECOND_ITEM[enumerators[0]]})"
    return first if second in ids and parent + first not in ids else None


#: A list marker's enumerator, followed by white space or by the next
#: marker of a chain: `(iii) `, `(A) `, the `(A)` of `(A)(1) The data`.
_MARKER_RE = re.compile(r"\(([0-9]+|[A-Za-z]{1,4})\)(?=\s|\()")
#: What may come before a list marker: the start of the text, a line break,
#: the end of a clause (`.`, `;`, `:`, a dash, the `]` of `(i) [Reserved]`,
#: then any closing quote: `...date];” and (B) Held`),
#: optionally then `and` or `or` (`...; and (ii) If an action`). Not a
#: word: `paragraph (a) of this section` and `(1) or (2)` are references,
#: not list items. A marker right after another (`(A)(1)`) is `lists_part`'s.
_BEFORE_MARKER_RE = re.compile(
    r"(?:^|[.;:\]\u2014\u2013\n][\u201d\u2019\u0022\)]*(?:\s*(?:and|or))?)\s*$"
)


#: The whole run of markers at one place: `(A)(1)`, `(b)(11)(iv)`.
_CHAIN_RE = re.compile(r"(?:\(([0-9]+|[A-Za-z]{1,4})\))+")
#: What follows a reference rather than a list item's text: punctuation, or
#: a joining word (`(b)(11)(iv)(B)(9) of this section`, `(ii) and (iv)`).
_AFTER_REFERENCE_RE = re.compile(r"[,;)]|\s+(?:of|and|or|through|to)\b")


def list_markers(text: str) -> list[tuple[int, str]]:
    """(position, enumerator) of each list marker in `text`, as `lists_part`
    reads them: see its docstring. A run of markers (`(A)(1)`) counts whole
    or not at all: it must start where a list item can, and what follows it
    must be the item's text, not a comma, semicolon or joining word, so
    `...; (b)(11)(iv)(B)(7)(iii), (iv), and (v); ... of this section` is a
    list of references, not of items (9b on PR 455)."""
    markers: list[tuple[int, str]] = []
    for chain in _CHAIN_RE.finditer(text):
        if not _BEFORE_MARKER_RE.search(text[: chain.start()]):
            continue
        after = text[chain.end() :]
        if not after[:1].isspace() and after:
            continue
        if _AFTER_REFERENCE_RE.match(after):
            continue
        markers += [
            (m.start(), m.group(1))
            for m in _MARKER_RE.finditer(text, chain.start(), chain.end() + 1)
        ]
    return markers


def lists_part(leaf: str, text: str, part: str) -> bool:
    """Whether `text`, a leaf's own text, lists `part` below it (80's 6235
    rule 2 on #438; #441): `(iii)` below `170.315(b)(1)` resolves when that
    criterion's text holds `(iii)` as a list item.

    - The text's **list markers** are read by `list_markers`: at its start,
      or after a line break or the end of a clause (`.`, `;`, `:`, a dash,
      the `]` of `[Reserved]`, a closing quote, optionally then `and`/`or`),
      a run of them (`(A)(1)`) whole, and followed by the item's text. A
      reference (`paragraph (a) of this section`, `paragraphs
      (a)(5)(iii)(A)(1) or (2)`, `...; (b)(11)(iv)(B)(7)(iii), (iv)`) is
      not one.
    - The leaf's own label at the start of its text (ONC's `(1)
      Computerized provider order entry`) is not one of its parts.
    - The markers are read as a **tree** (`_list_paths`), and the part is
      listed when the tree has an item at its path: `(ii)(A)(1)` needs an
      `(A)` under the `(ii)` and a `(1)` under that `(A)`, not the
      `(A)(1)` under `(iii)`.

    A text that lists nothing lists no part, so with this check on, every
    part below such a leaf is reported."""
    enumerators = _PAREN_RE.findall(part)
    if not enumerators or "".join(f"({e})" for e in enumerators) != part:
        return False
    body = text.strip()
    own = _PAREN_RE.findall(leaf)
    if own and body.startswith(f"({own[-1]})"):
        body = body[len(own[-1]) + 2 :]
    return tuple(enumerators) in _list_paths(e for _, e in list_markers(body))


def _list_paths(markers) -> set[tuple[str, ...]]:
    """The path of every item a run of list markers opens, read as a tree.

    Each marker is the next item of an open list, the deepest that it can
    follow (`(v)` after `(iv)`, `(8)` after `(7)`), or else the first item
    (`(1)`, `(a)`, `(i)`, `(A)`) of a new list under the item before it. A
    marker that is neither is not placed. Deepest first, because a kind
    recurs: in `(iv)(B)(7)(i)`...`(v)`, the `(v)` is the fifth item under
    `(7)`, and `(iv)`'s own `(v)` comes after `(B)`'s `(9)` closes (9b on
    PR 455)."""
    paths: set[tuple[str, ...]] = set()
    stack: list[str] = []
    for enumerator in markers:
        for depth in range(len(stack) - 1, -1, -1):
            if enumerator in _successors(stack[depth]):
                stack = [*stack[:depth], enumerator]
                break
        else:
            if stack and enumerator not in _SECOND_ITEM:
                continue
            stack = [*stack, enumerator]
        paths.add(tuple(stack))
    return paths


def _successors(enumerator: str) -> set[str]:
    """The enumerators that can follow `enumerator` in its list: `(3)` after
    `(2)`, `(C)` after `(B)`, `(iii)` after `(ii)`; `(i)`, `(v)` and `(x)` can
    be roman or a letter, so both readings are taken."""
    if enumerator.isdigit():
        return {str(int(enumerator) + 1)}
    found = set()
    if len(enumerator) == 1:
        found.add(chr(ord(enumerator) + 1))
    value = _roman(enumerator)
    if value is not None:
        found.add(_to_roman(value + 1))
    return found


def member_spelling(text: str) -> str:
    """`text` with each range separator written `)-(` and each list joiner
    `), (`: `CP-9(1) - (3)`, with an en or em dash, `to`, `through` or
    `thru`, reads `CP-9(1)-(3)`; `CP-9(1) and (3)` and `CP-9(1) & (3)` read
    `CP-9(1), (3)` (80's 6185 and 6197 on PR 437)."""
    return _JOINER_RE.sub("), (", _SEPARATOR_RE.sub(")-(", text))


def holds_member(text: str) -> bool:
    """Whether `text` holds a member-shaped part, `(3)` or `(iii)`: text
    after an id that does is never its qualifier, whatever joins them; it
    is a range, a list, or reported (80's 6197 on PR 437)."""
    return bool(_MEMBER_TOKEN_RE.search(text))


def catalog_families(controls, rule) -> dict[str, str]:
    """{id: the id of the family it belongs to} for one regulatory catalog.

    `rule` is its manifest (`registry.Framework`), declaring `family:`:

    - `section`: the section, `2.16(a)` -> `2.16`;
    - `criterion`: the criterion, `170.315(b)(1)` -> itself;
    - `structure`: the catalog's own nesting, which for HIPAA is the
      regulation's: each top-level entry a standard, its enhancements the
      standard's implementation specifications. A top-level entry titled
      "Implementation specification(s)" belongs to the standard before it.

    Then `family_overrides` (where the catalog and the regulation disagree)
    and `structure_only` (each its own family) apply. A family id is always
    a catalog id, so it has a crosswalk and a title.
    """
    families: dict[str, str] = {}
    standard = None
    for control in controls:
        cid = control.control_id
        if rule.family in ("section", "criterion"):
            for i in (cid, *(e.enhancement_id for e in control.enhancements)):
                families[i] = _head(rule.family, i)
            continue
        if _SPEC_TITLE_RE.match(control.title or "") and standard is not None:
            families[cid] = standard
        else:
            standard = families[cid] = cid
        for enhancement in control.enhancements:
            families[enhancement.enhancement_id] = families[cid]
    families.update({i: f for i, f in rule.family_overrides.items() if i in families})
    families.update({i: i for i in rule.structure_only if i in families})
    # A section head that is not itself an id (none ship today) stays a
    # family by name; `regulatory_families` returns only what is here.
    return families


def _head(unit: str, requirement_id: str) -> str:
    """`requirement_id`'s section, or its criterion (a section and two
    paragraph levels, falling back to the section for a shorter id)."""
    if unit == "criterion":
        match = _CRITERION_RE.match(requirement_id)
        if match:
            return match.group(0)
    return _SECTION_RE.match(requirement_id).group(0)


def regulatory_families(requirement_id: str, families: dict[str, str]) -> set[str]:
    """The families a citation of `requirement_id` stands for.

    - an id the catalog has: its family;
    - a statement part below one (`164.316(b)(1)(i)`): that id's family;
    - an **ancestor** the catalog does not carry, how a standard is cited
      (`164.308(a)(1)`, whose id is `164.308(a)(1)(i)`; `164.316(b)`): the
      families of every id beneath it. A section cited whole stands for all
      of its standards, so a change to any of them reaches the document.

    Empty when nothing resolves, so the citation is still reported.
    """
    if requirement_id in families:
        return {families[requirement_id]}
    inner = enclosing(requirement_id, lambda i: i in families)
    if inner in families:
        return {families[inner]}
    return {f for i, f in families.items() if i.startswith(requirement_id + "(")}


def families_for(controls, rules=None) -> dict[str, dict[str, str]]:
    """{catalog key: `catalog_families`} for each loaded catalog whose
    manifest declares a family rule. `rules` defaults to the manifests on
    disk (`registry.declared_family_rules`), read once."""
    if rules is None:
        rules = _declared_rules()
    by_key: dict[str, list] = {}
    for control in controls:
        by_key.setdefault(normalize_framework(control.framework), []).append(control)
    return {key: catalog_families(ctl, rules[key]) for key, ctl in by_key.items() if key in rules}


def leaf_texts_for(controls, checks=None) -> dict[str, dict[str, str]]:
    """{catalog key: {id: its own text}} for each loaded catalog whose
    manifest says `leaf_parts: listed` (#441): what `stands_below` reads a
    part against. `checks` defaults to the manifests on disk
    (`registry.declared_leaf_checks`), read once. A control's text is its
    statement, an enhancement's its description, a requirement's its
    statement."""
    if checks is None:
        checks = _declared_leaf_checks()
    texts: dict[str, dict[str, str]] = {}
    for control in controls:
        key = normalize_framework(control.framework)
        if key not in checks:
            continue
        own = texts.setdefault(key, {})
        own[control.control_id] = control.control_statement or ""
        own.update({e.enhancement_id: e.description or "" for e in control.enhancements})
        own.update({r.requirement_id: r.statement or "" for r in control.requirements})
    return texts


_LEAF_CHECKS: dict | None = None


def _declared_leaf_checks() -> dict:
    global _LEAF_CHECKS
    if _LEAF_CHECKS is None:
        from policyforge.frameworks.registry import config_or_defaults, declared_leaf_checks

        _LEAF_CHECKS = declared_leaf_checks(config_or_defaults("leaf checks were read"))
    return _LEAF_CHECKS


_RULES: dict | None = None


def _declared_rules() -> dict:
    global _RULES
    if _RULES is None:
        from policyforge.frameworks.registry import config_or_defaults, declared_family_rules

        _RULES = declared_family_rules(config_or_defaults("family rules were read"))
    return _RULES

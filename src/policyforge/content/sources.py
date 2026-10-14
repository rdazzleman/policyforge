"""A Standard's citation form and its source blocks (#17, part 2a).

The user's review of a real 20-topic run asked for two things a Standard
didn't do (80's comments 5902 and 5928 on #17, ruled in 6048):

* **One short tag per requirement**, in the form the user confirmed:
  `[HITRUST 11.a, 11.c | NIST IR-1, IR-8 | ARC-AMPE]`. 800-53's ids appear
  once, under `NIST`. A tailoring of 800-53 (ARC-AMPE, FedRAMP, GovRAMP) is
  named as a baseline, and keeps an id of its own only where the tag doesn't
  already cite that id under `NIST`, e.g. `ARC-AMPE RA-5(11)` beside `NIST
  RA-5`. Other frameworks keep their own ids. `normalize_tag`.
* **The source wording under the summary**, for an auditor: one collapsible
  block per framework under each section, listing every requirement the
  section's tags cite with the catalog's own text. `add_source_blocks`.

Both run after the model call, deterministically, from the run's catalogs:
the model never restates source text, so a quote can't be a paraphrase, and
no licensed text is sent to a model at this step. Licensed text is quoted
only when the organisation sets `standards.quote_licensed` (the default
waits on the user; 80's 6048).

**Source text is never read as the organisation's obligations.** It says
"The organization shall ...", and a block carries no tag, so `deontic` would
read it as uncited obligations. Each block is marked
(`SOURCE_BLOCK_OPEN`), and `blank_source_blocks` blanks it, line for line,
for every reader that analyses sentences. A block whose close was lost is
not blanked, and `check` names it (`unclosed_source_blocks`).

**A quote must be the catalog's text, byte for byte** (#17 part 2b, 80's
6048). `check` re-reads every block against the catalogs on disk
(`fidelity_findings`): a quote that differs, an id the catalog doesn't hold,
a title or baseline the catalog doesn't carry, a count that disagrees with
the entries, an unlicensed entry without its quote, or any line that isn't
a shape `_block` writes (1d on PR 431) is an ERROR. A block is plain
markdown a person can edit, so the marker alone proves nothing about what it
quotes; this is what does.

Confluence gets each block as its `expand` macro, titled with the block's
summary (`export.confluence_exporter`), and `pull` turns that macro back
into a block (`export.confluence_importer`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from policyforge.mapping.crosswalk import NIST_ANCHOR, normalize_framework

#: The opening line of a source block, the one structural marker `check` and
#: `deontic` skip. An attribute rather than a class, so no renderer styles it.
SOURCE_BLOCK_OPEN = '<details data-policyforge="source">'
SOURCE_BLOCK_CLOSE = "</details>"

#: The name 800-53 is cited under (5928): `NIST IR-1`.
NIST_NAME = "NIST"

#: A block's summary line: `<summary>Source: NIST 800-53 (2)</summary>`.
SUMMARY_RE = re.compile(r"^<summary>(?P<title>Source: (?P<name>.+) \((?P<count>\d+)\))</summary>$")

_HEADING_RE = re.compile(r"^#{1,6}\s")
_FENCE_RE = re.compile(r"^(`{3,})")


def _source_blocks(text: str) -> tuple[list[tuple[int, int]], list[int]]:
    """(the (open, close) line indexes of each closed source block, the
    line index of each open that has no close).

    **A block ends at its own close, and only there** (1d on PR 430). The
    search for it stops at a heading or at another source-block open, so
    one deleted `</details>` leaves THAT block open rather than reaching to
    the next block's close and swallowing every section between them,
    which would hide their obligations from `deontic`. The assumption the
    bound makes: a source block holds no heading and no block open outside
    a fence. `add_source_blocks` fences every quote, and a fenced line is
    never read as a heading, so quoted text starting with `#` can't end a
    block early.
    """
    lines = text.split("\n")
    spans: list[tuple[int, int]] = []
    unclosed: list[int] = []
    i = 0
    outer = ""  # a fence the document itself opened: no block starts inside it
    while i < len(lines):
        stripped = lines[i].strip()
        if outer:
            if re.fullmatch(rf"`{{{len(outer)},}}", stripped):
                outer = ""
            i += 1
            continue
        opened = _FENCE_RE.match(stripped)
        if opened:
            # A block written inside a code example is an example, not a
            # block (b5 on PR 431): it was exported as a placeholder token.
            outer = opened.group(1)
            i += 1
            continue
        if lines[i].rstrip() != SOURCE_BLOCK_OPEN:
            i += 1
            continue
        fence = ""
        close = None
        for j in range(i + 1, len(lines)):
            stripped = lines[j].strip()
            if fence:
                if re.fullmatch(rf"`{{{len(fence)},}}", stripped):
                    fence = ""
                continue
            opened = _FENCE_RE.match(stripped)
            if opened:
                fence = opened.group(1)
            elif stripped == SOURCE_BLOCK_CLOSE:
                close = j
                break
            elif _HEADING_RE.match(lines[j]) or stripped == SOURCE_BLOCK_OPEN:
                break
        if close is None:
            unclosed.append(i)
            i += 1
        else:
            spans.append((i, close))
            i = close + 1
    return spans, unclosed


def blank_source_blocks(text: str) -> str:
    """`text` with every closed source block's lines emptied, line numbers
    kept, so a reader that analyses sentences never reads quoted source
    text. An unclosed block is left as it is: its quote is then read as
    prose, the loud direction, and `check` names it."""
    spans, _ = _source_blocks(text)
    if not spans:
        return text
    lines = text.split("\n")
    for start, end in spans:
        for k in range(start, end + 1):
            lines[k] = ""
    return "\n".join(lines)


def source_block_spans(text: str) -> list[tuple[int, int]]:
    """The (open, close) line indexes of each closed source block."""
    return _source_blocks(text)[0]


def has_source_blocks(text: str) -> bool:
    return bool(_source_blocks(text)[0])


def unclosed_source_blocks(text: str) -> list[int]:
    """The 1-based line of each source-block open with no close of its own."""
    return [i + 1 for i in _source_blocks(text)[1]]


def tailorings(scope) -> frozenset[str]:
    """The catalogs in `scope` that tailor 800-53: every id 800-53-shaped.

    Measured over the bundled catalogs: ARC-AMPE (402 ids) and FedRAMP (85)
    hold nothing else, and every other catalog holds no such id. No manifest
    says which catalogs are tailorings, so their ids say it.
    """
    from policyforge.topics.anchoring import is_800_53_shaped

    return frozenset(
        key
        for key, ids in scope.index.items()
        if key != NIST_ANCHOR and ids and all(is_800_53_shaped(i) for i in ids)
    )


@dataclass(frozen=True)
class _Cited:
    written: str  # the framework name as the tag wrote it
    catalog: str  # the catalog it resolves to, "" if none
    id: str  # "" for a baseline reference
    qualifier: str = ""
    #: The ids this citation stands for in its catalog (`standing_for`).
    standing: tuple[str, ...] = ()


def _names(scope) -> list[str]:
    from policyforge.content.tags import known_framework_names

    return sorted({str(n) for n in scope.names} | known_framework_names(), key=len, reverse=True)


def _resolved(tag: str, scope) -> list[_Cited] | None:
    """Each part of `tag`, resolved in `scope`; None if any citation in it
    resolves to nothing, so the caller leaves the tag as written."""
    from policyforge.content.tags import tag_parts
    from policyforge.topics.satisfies import read_citation, resolve_framework

    names = _names(scope)
    out: list[_Cited] = []
    for part in tag_parts(tag, names, lambda w: bool(resolve_framework(w, scope.index))):
        if part.baseline:
            out.append(_Cited(part.framework, resolve_framework(part.framework, scope.index), ""))
            continue
        # A range or a comma list is kept as written in the tag, standing for
        # each member (#421).
        framework, requirement_id, cited, qualifier = read_citation(part.citation, names, scope)
        catalog, standing = "", []
        for member in cited:
            catalog, each = scope.standing_for(framework, member)
            if not each:
                return None
            standing += [i for i in each if i not in standing]
        if not standing:
            return None
        out.append(_Cited(framework, catalog, requirement_id, qualifier, tuple(standing)))
    return out


def _entry(cited: _Cited) -> str:
    return f"{cited.id} {cited.qualifier}".strip()


def _render_group(name: str, entries: list[str]) -> str:
    if not entries:
        return name
    # Joined by commas only when every entry reads back as its own citation
    # (`content.tags._comma_citations`): it opens with an id-shaped token.
    if all(any(ch.isdigit() for ch in e.split()[0]) and not e.startswith("(") for e in entries):
        return f"{name} {', '.join(entries)}"
    return " | ".join(f"{name} {e}" for e in entries)


def normalize_tag(tag: str, scope) -> str:
    """`tag` in the confirmed citation form (5928 on #17, 80's 6048 Q1).

    - Every 800-53 id is listed once, under `NIST`.
    - A tailoring's id is dropped when the tag cites the same id under
      `NIST`, and kept when it doesn't; a tailoring left with no ids is
      written bare, as a baseline.
    - Other frameworks keep their ids, under the name the tag wrote.
    - Groups with ids come first (other frameworks, then `NIST`, then
      tailorings), and bare names last, so the tag still opens with a name
      and an id and `content.tags.SOURCE_TAG_RE` still reads it.

    A tag with any citation that doesn't resolve is returned as written, so
    `check` reports it as the model wrote it. Never drops a citation: every
    citation of the input is in the output, or is a tailoring id whose 800-53
    twin is (`covered`).

    **It does drop a dropped id's qualifier text** (b5 on PR 430, measured
    over the committed corpus: 1,471 of 4,296 rewritten tags). 1,458 are
    `AE Mandatory` on an ARC-AMPE id whose 800-53 twin is cited, which is
    the catalog's `baseline` field: the section's source block shows it on
    that id's ARC-AMPE entry, `RA-5 (AE Mandatory)` (80's 6075 on PR 430).
    13 are notes, such as a parameter value, that no catalog field holds,
    so no block shows them.
    """
    cited = _resolved(tag, scope)
    if cited is None or not any(c.id for c in cited):
        return tag
    tailored = tailorings(scope)
    nist: list[str] = []
    others: dict[str, tuple[str, list[str]]] = {}
    tails: dict[str, tuple[str, list[str]]] = {}
    for c in cited:
        if c.catalog == NIST_ANCHOR and c.id and _entry(c) not in nist:
            nist.append(_entry(c))
    nist_ids = {c.id for c in cited if c.catalog == NIST_ANCHOR}
    for c in cited:
        if c.catalog == NIST_ANCHOR:
            continue
        groups = tails if c.catalog in tailored else others
        _, entries = groups.setdefault(c.catalog or c.written, (c.written, []))
        keep = c.id and not (c.catalog in tailored and c.id in nist_ids)
        if keep and _entry(c) not in entries:
            entries.append(_entry(c))
    with_ids = [g for g in others.values() if g[1]]
    with_ids += [(NIST_NAME, nist)] if nist else []
    with_ids += [g for g in tails.values() if g[1]]
    bare = [g for g in (*others.values(), *tails.values()) if not g[1]]
    return "[" + " | ".join(_render_group(n, e) for n, e in (*with_ids, *bare)) + "]"


def normalize_tags(text: str, scope) -> str:
    """Every source tag in `text` in the confirmed form (`normalize_tag`)."""
    from policyforge.content.tags import SOURCE_TAG_RE

    return SOURCE_TAG_RE.sub(lambda m: normalize_tag(m.group(0), scope), text)


# ---- comparing citations, not tag strings -------------------------------------


def citation_keys(tag: str, scope) -> set[tuple[str, str]]:
    """(catalog, id) for each citation in `tag`. A citation that resolves to
    nothing is keyed by its text as written, `("", "XYZ 1.1")`, so it still
    compares; a baseline reference cites nothing and has no key.

    The unit `check` and grounding compare a document with its synthesis by,
    since `normalize_tag` rewrites the tag's text and keeps its citations."""
    from policyforge.content.tags import tag_parts
    from policyforge.topics.satisfies import read_citation, resolve_framework

    names = _names(scope)
    keys: set[tuple[str, str]] = set()
    for part in tag_parts(tag, names, lambda w: bool(resolve_framework(w, scope.index))):
        if part.baseline:
            continue
        framework, requirement_id, cited, _ = read_citation(part.citation, names, scope)
        if cited != [requirement_id]:
            # A range or a comma list: each member's key (#421).
            keys.update((scope.standing_for(framework, i)[0], i) for i in cited)
            continue
        catalog, standing = scope.standing_for(framework, requirement_id)
        keys.add((catalog, requirement_id) if standing else ("", part.citation))
    return keys


def covered(key: tuple[str, str], present: set[tuple[str, str]], tailored) -> bool:
    """Whether `present` carries `key`: itself, or, for a tailoring's id,
    the same id under 800-53 (the id `normalize_tag` dropped for it)."""
    catalog, requirement_id = key
    return key in present or (catalog in tailored and (NIST_ANCHOR, requirement_id) in present)


# ---- source blocks --------------------------------------------------------------


@dataclass(frozen=True)
class _Source:
    title: str
    text: str
    #: The catalog's baseline for this id (`AE Mandatory` on every ARC-AMPE
    #: control); shown for a tailoring's entry (80's 6075 on PR 430).
    baseline: str = ""


@dataclass
class SourceIndex:
    """The run's catalogs as a block reads them: each requirement's title and
    text by (catalog, id), each catalog's display name, and which are
    licensed."""

    entries: dict[tuple[str, str], _Source] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)
    licensed: frozenset[str] = frozenset()


def source_index_of(frameworks) -> SourceIndex:
    """`source_index` over discovered `frameworks` (`frameworks.registry`),
    each read from its controls.json, with a catalog that doesn't declare
    itself public counted licensed, as the registry reads a manifest (fail
    closed). What `generate` quotes from, and what `check` holds a quote to."""
    from policyforge.ingest.schema import load_controls

    controls: list = []
    licensed: set[str] = set()
    for framework in frameworks:
        rows = load_controls(framework.controls_path)
        controls += rows
        if not framework.redistributable:
            licensed |= {normalize_framework(c.framework) for c in rows}
    return source_index(controls, licensed)


def _with_parameters(text: str, item) -> str:
    lines = [text.strip()] if text.strip() else []
    values = getattr(item, "parameter_values", None) or {}
    if values:
        lines.append("Parameter values: " + "; ".join(f"{k}: {v}" for k, v in values.items()))
    extra = (getattr(item, "additional_requirements", "") or "").strip()
    if extra:
        lines.append(extra)
    return "\n\n".join(lines)


def source_index(controls, licensed_keys=()) -> SourceIndex:
    """Index `controls` for `add_source_blocks`. `licensed_keys` are the
    catalog keys (`normalize_framework`) whose text is licensed."""
    index = SourceIndex(licensed=frozenset(licensed_keys))
    for control in controls:
        key = normalize_framework(control.framework)
        index.display.setdefault(key, control.framework)
        index.entries.setdefault(
            (key, control.control_id),
            _Source(
                control.title,
                _with_parameters(control.control_statement, control),
                (control.baseline or "").strip(),
            ),
        )
        for enhancement in control.enhancements:
            index.entries.setdefault(
                (key, enhancement.enhancement_id),
                _Source(
                    enhancement.title,
                    _with_parameters(enhancement.description, enhancement),
                    (enhancement.baseline or "").strip(),
                ),
            )
        for requirement in control.requirements:
            index.entries.setdefault(
                (key, requirement.requirement_id),
                _Source(control.title, requirement.statement.strip()),
            )
    return index


#: Emphasised with `*`, the form markdownify writes, so a block pulled back
#: from Confluence keeps the note byte for byte (mdformat keeps either). The
#: `_` spelling 2a wrote is still read (`_LICENSED_NOTES`).
_LICENSED_NOTE = (
    "*Licensed source text is not quoted here. Set `standards.quote_licensed: true` "
    "in config to include it.*"
)
_LICENSED_NOTES = frozenset({_LICENSED_NOTE, "_" + _LICENSED_NOTE[1:-1] + "_"})


def _fenced(text: str) -> list[str]:
    """`text` as a fenced block, verbatim. A fence, not a blockquote: the
    provenance stamp runs mdformat over the whole document, and mdformat
    renumbers a quoted `a. 1. 2.` list and re-nests its lines, so a
    blockquote's text stops being the catalog's (measured). A fenced block
    is the one construct it keeps byte for byte. The fence is longer than
    any run of backticks in the text."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}text", *(line.rstrip() for line in text.splitlines()), fence]


def _block(
    catalog: str, ids: list[str], sources: SourceIndex, quote_licensed: bool, tailoring: bool
) -> str:
    name = sources.display.get(catalog, catalog)
    quote = quote_licensed or catalog not in sources.licensed
    lines = [SOURCE_BLOCK_OPEN, f"<summary>Source: {name} ({len(ids)})</summary>", ""]
    if not quote:
        lines += [_LICENSED_NOTE, ""]
    for requirement_id in ids:
        source = sources.entries.get((catalog, requirement_id))
        title = f" {source.title}" if source and source.title else ""
        # A tailoring's baseline, which the tag rewrite drops with its id (80's
        # 6075 on PR 430): `RA-5 (AE Mandatory)`, whether CMS requires it here.
        baseline = f" ({source.baseline})" if tailoring and source and source.baseline else ""
        lines += [f"**{requirement_id}{baseline}**{title}", ""]
        if quote and source and source.text:
            lines += [*_fenced(source.text), ""]
    lines.append(SOURCE_BLOCK_CLOSE)
    return "\n".join(lines)


def _section_ids(text: str, scope, sources: SourceIndex) -> dict[str, list[str]]:
    """{catalog: ids} each section's tags cite, in order of first citation.

    A tailoring's text is listed where its tag names it, for each 800-53 id
    of that tag the tailoring holds with different text (an ARC-AMPE
    parameter value) or with a baseline (`AE Mandatory`, which the tag
    rewrite dropped; 80's 6075), and for any id of its own (5928)."""
    from policyforge.content.tags import SOURCE_TAG_RE

    tailored = tailorings(scope)
    found: dict[str, list[str]] = {}

    def add(catalog: str, requirement_id: str) -> None:
        ids = found.setdefault(catalog, [])
        if requirement_id not in ids:
            ids.append(requirement_id)

    for tag in SOURCE_TAG_RE.findall(text):
        cited = _resolved(tag, scope)
        if not cited:
            continue
        nist_ids = [i for c in cited if c.catalog == NIST_ANCHOR for i in c.standing]
        for c in cited:
            for requirement_id in c.standing:
                add(c.catalog, requirement_id)
            if c.catalog in tailored:
                for requirement_id in nist_ids:
                    own = sources.entries.get((c.catalog, requirement_id))
                    hub = sources.entries.get((NIST_ANCHOR, requirement_id))
                    # Listed where its text differs, and wherever it has a
                    # baseline, so the qualifier the tag dropped is kept here.
                    if own and (hub is None or own.text != hub.text or own.baseline):
                        add(c.catalog, requirement_id)
    return {catalog: ids for catalog, ids in found.items() if ids}


def add_source_blocks(
    text: str, sources: SourceIndex, scope, *, quote_licensed: bool = False
) -> tuple[str, int]:
    """`text` with a source block per cited framework at the end of each
    section (a heading and what follows it, up to the next heading), and the
    number of blocks added. A section citing nothing gets none, and text
    that already has source blocks is returned unchanged."""
    if has_source_blocks(text):
        return text, 0
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if _HEADING_RE.match(line)]
    bounds = list(zip([0, *starts], [*starts, len(lines)], strict=True))
    out: list[str] = []
    added = 0
    for start, end in bounds:
        section = lines[start:end]
        while section and not section[-1].strip():
            section = section[:-1]
        blocks = [
            _block(catalog, ids, sources, quote_licensed, catalog in tailorings(scope))
            for catalog, ids in _section_ids("\n".join(section), scope, sources).items()
        ]
        trailing = lines[start + len(section) : end]
        out += section
        if blocks:
            out += ["", "\n\n".join(blocks)]
            added += len(blocks)
        out += trailing if trailing else ([""] if blocks and end < len(lines) else [])
    return "\n".join(out), added


# ---- fidelity: a quote is the catalog's text (#17 part 2b) ---------------------

_ENTRY_RE = re.compile(r"^\*\*(?P<label>[^*]+)\*\*(?: (?P<title>.*))?$")


@dataclass
class _Entry:
    line: int  # 1-based
    id: str
    baseline: str
    title: str
    quote: str | None  # None when the entry quotes nothing (licensed, not opted in)


@dataclass
class _Parsed:
    line: int  # 1-based, the open line
    name: str
    count: int | None
    entries: list[_Entry] = field(default_factory=list)
    #: The block carries `_LICENSED_NOTE`, so an entry may quote nothing.
    licensed_note: bool = False
    #: 1-based lines that are none of the shapes `_block` writes.
    stray: list[int] = field(default_factory=list)


def _split_label(label: str) -> tuple[str, str]:
    """`RA-5 (AE Mandatory)` -> ("RA-5", "AE Mandatory"); `AC-2(1)` -> ("AC-2(1)", "")."""
    if label.endswith(")") and " (" in label:
        at = label.rindex(" (")
        return label[:at], label[at + 2 : -1]
    return label, ""


def _fence_end(lines: list[str], at: int, end: int) -> int:
    """The index of the line closing the fence opened at `at`, or `end`."""
    fence = _FENCE_RE.match(lines[at].strip()).group(1)
    for k in range(at + 1, end):
        if re.fullmatch(rf"`{{{len(fence)},}}", lines[k].strip()):
            return k
    return end


def parse_source_blocks(text: str) -> list[_Parsed]:
    """Each closed source block of `text`, read by the grammar `_block`
    writes, and nothing looser (1d on PR 431): a summary line; then blank
    lines, at most the licensed note, and entries, each an `**<id>**
    <title>` line followed by at most one fenced quote. **Any other line is
    `stray`**, a fence included whole: a block is blanked for `deontic`, so
    text there that nothing checks could hide an obligation."""
    lines = text.split("\n")
    blocks: list[_Parsed] = []
    for start, end in source_block_spans(text):
        summary = SUMMARY_RE.match(lines[start + 1].strip()) if start + 1 < end else None
        block = _Parsed(
            start + 1,
            summary.group("name") if summary else "",
            int(summary.group("count")) if summary else None,
        )
        i = start + 2 if summary else start + 1
        while i < end:
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue
            if stripped in _LICENSED_NOTES:
                block.licensed_note = True
                i += 1
                continue
            entry = _ENTRY_RE.match(stripped)
            if not entry:
                if _FENCE_RE.match(stripped):
                    close = _fence_end(lines, i, end)
                    block.stray += list(range(i + 1, min(close, end - 1) + 2))
                    i = close + 1
                else:
                    block.stray.append(i + 1)
                    i += 1
                continue
            requirement_id, baseline = _split_label(entry.group("label"))
            quote = None
            j = i + 1
            while j < end and not lines[j].strip():
                j += 1
            if j < end and _FENCE_RE.match(lines[j].strip()):
                close = _fence_end(lines, j, end)
                quote = "\n".join(lines[j + 1 : close])
                following = close + 1
            else:
                following = i + 1
            block.entries.append(
                _Entry(i + 1, requirement_id, baseline, entry.group("title") or "", quote)
            )
            i = following
        blocks.append(block)
    return blocks


def _as_quoted(text: str) -> str:
    """The catalog's text as `_fenced` writes it: each line right-stripped."""
    return "\n".join(line.rstrip() for line in text.splitlines())


def fidelity_findings(text: str, sources: SourceIndex) -> list[tuple[int, str, bool]]:
    """(line, message, is_error) for each way a source block of `text`
    disagrees with the catalogs in `sources`. A block naming a catalog that
    isn't there is a warning: its quotes can't be checked here, which is
    said, not passed. Its shape (stray lines, the count) is checked anyway."""
    by_name = {name: key for key, name in sources.display.items()}
    found: list[tuple[int, str, bool]] = []
    for block in parse_source_blocks(text):
        # The block's shape first, whatever its catalog (1d on PR 431): a
        # name no catalog has, or no summary at all, must not let a stray
        # line ride on a warning.
        for line in block.stray:
            found.append((line, "source block holds a line no catalog wrote", True))
        if not block.name:
            found.append(
                (block.line, "source block has no `Source: <catalog> (<n>)` summary", True)
            )
            continue
        if block.count != len(block.entries):
            found.append(
                (
                    block.line,
                    f"source block for {block.name} says {block.count} and lists "
                    f"{len(block.entries)}",
                    True,
                )
            )
        catalog = by_name.get(block.name)
        if catalog is None:
            found.append(
                (
                    block.line,
                    f"source block quotes {block.name}, which no catalog on disk is, "
                    "so its quotes weren't checked",
                    False,
                )
            )
            continue
        for entry in block.entries:
            source = sources.entries.get((catalog, entry.id))
            where = f"{block.name} {entry.id}"
            if source is None:
                found.append(
                    (
                        entry.line,
                        f"source block quotes {where}, which its catalog doesn't hold",
                        True,
                    )
                )
                continue
            if entry.baseline and entry.baseline != source.baseline:
                found.append(
                    (
                        entry.line,
                        f"source block labels {where} ({entry.baseline}), and the catalog "
                        f"says {source.baseline or 'no baseline'}",
                        True,
                    )
                )
            # Stripped: the entry line is read stripped, and 800-53 SA-4(7)'s
            # own title ends in a space (b5 on PR 431).
            if entry.title.strip() != source.title.strip():
                found.append(
                    (
                        entry.line,
                        f"source block titles {where} {entry.title!r}, and the catalog "
                        f"says {source.title!r}",
                        True,
                    )
                )
            # The note excuses a missing quote only for a catalog that IS
            # licensed; on a public one it would hide a deleted quote (b5).
            excused = block.licensed_note and catalog in sources.licensed
            if entry.quote is None and source.text and not excused:
                found.append((entry.line, f"source block lists {where} without its quote", True))
            if entry.quote is not None and entry.quote != _as_quoted(source.text):
                found.append(
                    (entry.line, f"source block's quote of {where} isn't the catalog's text", True)
                )
    return found

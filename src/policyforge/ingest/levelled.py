"""A control catalog stated per level, assembled from records (#32).

The public, file-neutral half of what was the HITRUST importer (the other
half is in the separately distributed licensed-framework plugin). It holds what
a catalog with HITRUST CSF's shape IS, once somebody has read the rows out
of a file: the Category / Objective / Control Reference / Requirement
hierarchy, maturity levels against regulatory overlays, the rules for
joining a level label to the name it was cut from (#15, #25), the
one-id-one-requirement rule, and the authoritative-source crosswalk a
mapping cell holds.

**How a vendor's file is read is not here.** That lives in the licensed
parsers, which the user's decision keeps out of the public tool (#32):
their SSRS column detection, caption handling and library counts. What
`generate-parser` drafts for a customer's own export is a reader too: it
fills in a `Record` per row and calls `build_controls`, so the assembly is
the same for every export, whoever wrote the reader.

Everything here works on strings a reader has already taken out of a file.
Nothing here opens one.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .schema import MATURITY, OVERLAY, Control, Requirement

FRAMEWORK = "HITRUST-CSF"

#: `NN.a <control title>`, `NN.aa <control title>`: digits, a dot, one or two
#: letters, then the title.
REFERENCE_RE = re.compile(r"^\s*(\d{1,2}\.[a-z]{1,2})\s+(.*\S)?\s*$")

#: `NN.NN <objective title>`; the first part may be one digit (`N.NN`).
OBJECTIVE_RE = re.compile(r"^\s*(\d{1,2}\.\d{2})(?![\d.])\s+(.*\S)?\s*$")

#: `NN.0 - <category title>`. The separator is a dash here and whitespace in
#: the other two tiers, which is a report convention rather than a rule, so
#: both are accepted.
#:
#: The lookahead is what keeps a category from swallowing an objective:
#: without it `NN.0` matches the opening of `NN.01 <objective title>`, and
#: column detection then reads the objective column as the category column
#: and folds every objective into its category.
# The en dash is deliberate: SSRS renders the category separator as one.
CATEGORY_RE = re.compile(r"^\s*(\d{1,2}\.0)(?![\d.])\s*[-–]?\s*(.*\S)?\s*$")  # noqa: RUF001

LEVEL_RE = re.compile(r"^\s*Level\s+(\S.*?)\s*$", re.IGNORECASE)

#: Only these three are the maturity ladder. Everything else under `Level `
#: is an overlay, and treating an unrecognised level as an overlay is the
#: safe default: a new regulatory level read as maturity would silently
#: join an ordered scale it has no place on.
MATURITY_NAMES = {"1", "2", "3"}

#: Where the level column is cut in an SSRS CSV export.
LEVEL_TRUNCATION = 36


class HitrustBuildError(ValueError):
    """The export parsed, but building a catalog from it would merge two
    different requirements or give one the wrong level. Nothing is written."""


def _split(pattern: re.Pattern[str], value: str) -> tuple[str, str]:
    match = pattern.match(value or "")
    if not match:
        return "", " ".join((value or "").split())
    return match.group(1), " ".join((match.group(2) or "").split())


def parse_reference(value: str) -> tuple[str, str]:
    """`NN.a <control title>` -> `("NN.a", "<control title>")`."""
    return _split(REFERENCE_RE, value)


def parse_objective(value: str) -> tuple[str, str]:
    """`NN.NN <objective title>` -> `("NN.NN", "<objective title>")`."""
    return _split(OBJECTIVE_RE, value)


def parse_category(value: str) -> tuple[str, str]:
    """`NN.0 - <category title>` -> `("NN.0", "<category title>")`."""
    return _split(CATEGORY_RE, value)


def parse_level(value: str) -> tuple[str, str]:
    """`Level FedRAMP` -> `("Level FedRAMP", "overlay")`.

    The label is returned verbatim rather than reduced to its name, because
    it is what the export says and what an assessor will search for.
    """
    label = " ".join((value or "").split())
    match = LEVEL_RE.match(label)
    if not match:
        return label, OVERLAY
    return label, MATURITY if match.group(1).strip() in MATURITY_NAMES else OVERLAY


def level_may_be_truncated(label: str) -> bool:
    """Whether a level label has the length SSRS clips the column to.

    Exactly 36 characters, or 35 when the cut fell just after a space that
    `rstrip` then removed. Anything shorter was not cut, and anything longer
    did not come from the clipped column (#15: `Level FedRAMP`, 13
    characters, was being merged into `Level FedRAMP KSI`).
    """
    return LEVEL_TRUNCATION - 1 <= len((label or "").strip()) <= LEVEL_TRUNCATION


def merge_level_names(labels: list[str]) -> dict[str, str]:
    """Map a clipped level label onto the full name it was cut from.

    A CSV cuts the level column at 36 characters. Given every level-cell
    value an export holds, this rejoins a clipped one to the full name when
    that name also appears uncut in some level cell, so `Level Digital
    Personal Data Protecti` and `Level Digital Personal Data Protection Act`
    do not become two levels. It is given level cells only, not the caption
    columns beside them, so a name clipped in every row has nothing to
    rejoin to and stays clipped (#27).

    **Only a label of the clipped length is merged** (`level_may_be_truncated`),
    and only onto the one longer label that starts with it. A shorter label
    that merely prefixes another (`Level FedRAMP`, `Level FedRAMP KSI`) is a
    level of its own (#15). A clipped label that two longer labels both start
    with could be either, so it is refused, naming all three.

    **Known limit:** a real level whose full name happens to be 35 or 36
    characters, and is the start of another level's name, is merged into it.
    Nothing in an export tells the two apart. If one control is stated at
    both, the id check in `build_controls` refuses it.
    """
    longest: dict[str, str] = {}
    for la in labels:
        if la and la.strip():
            cleaned = " ".join(la.split())
            if len(la.strip()) > len(longest.get(cleaned, "")):
                longest[cleaned] = la.strip()
    resolved: dict[str, str] = {}
    for label in sorted(longest, key=len):
        resolved[label] = label
        if not level_may_be_truncated(longest[label]):
            continue
        fuller = sorted(o for o in longest if len(o) > len(label) and o.startswith(label))
        if len(fuller) > 1:
            raise HitrustBuildError(
                f"The level label {label!r} is cut at {LEVEL_TRUNCATION} characters, and "
                f"{len(fuller)} longer level names start with it ({', '.join(map(repr, fuller))}), "
                "so which level it was cut from can't be told. Refusing rather than guessing."
            )
        if fuller:
            resolved[label] = fuller[0]
    return resolved


def split_lines(cell: str) -> list[str]:
    """One list entry per line of a factor or mapping cell.

    CSV exports use CRLF inside quoted cells; the MHTML rendering uses
    `<br/>`, which an HTML reader turns into a newline before it reaches
    here. Blank entries and the non-breaking spaces SSRS pads empty cells
    with are dropped.
    """
    if not cell:
        return []
    text = str(cell).replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        # U+00A0 is what SSRS pads an otherwise empty cell with. Written
        # as an escape rather than literally, so the source never carries
        # a character indistinguishable from a space to the next reader.
        stripped = line.replace("\xa0", " ").strip()
        if stripped:
            out.append(stripped)
    return out


#: A prefix is treated as a complete source name once this many different
#: tokens are seen following it. Identifiers vary wildly and source names do
#: not, so the count separates them. Six is low enough to catch a source
#: cited only a handful of times, and high enough that a two-word source
#: name is not cut after its first word.
MIN_BRANCH = 6

#: Source names never run this long; the cap stops a pathological line from
#: walking the whole string.
MAX_SOURCE_TOKENS = 12

#: Trailing marks that belong to the identifier rather than the source, and
#: which HITRUST writes with a space before them ("HIPAA Security Rule
#: § 164.308").
_SOURCE_TRIM = " §¶#:,-–"  # noqa: RUF001 - the en dash occurs in the data

#: A standard's *number* is part of its name, not the start of a citation
#: into it: `27002:2022` in `ISO/IEC 27002:2022 8(2)`, `23894` in `ISO/IEC
#: 23894`. Branching alone cannot see that, because a publisher who issues
#: many standards ("ISO/IEC", "NIST SP") branches at exactly the point the
#: name is still incomplete — which would file every ISO citation under a
#: source called `ISO/IEC`, collapsing 27001, 27002 and 27799 into one
#: framework.
#:
#: Three leading digits at minimum, so that short section numbers stay on
#: the identifier side: `1.08` under HHS CPGs is a citation, `27001:2022`
#: is a standard.
_STANDARD_NUMBER_RE = re.compile(r"^\d{3,5}(?:[:\-.]\d{1,4})*$")


def learn_sources(lines: list[str], *, min_branch: int = MIN_BRANCH) -> set[str]:
    """Learn the authoritative-source vocabulary from the mapping lines.

    Walks each line from its first token, extending the candidate source
    while what follows is predictable, and stopping at the first prefix with
    `min_branch` distinct continuations. `NIST SP 800-53 r5` survives that
    walk because only `r5` and `r4` ever follow `NIST SP 800-53`, and the
    walk halts there because hundreds of different control ids follow `r5`.

    Learned rather than listed on purpose. A hard-coded vocabulary would
    have to be updated every CSF release, would be wrong for the community
    and de-identification supplements, and would mean shipping a piece of
    HITRUST's own content in this repository.
    """
    children: dict[str, set[str]] = defaultdict(set)
    tokenized = [line.split(" ") for line in lines if line]
    for tokens in tokenized:
        for size in range(1, min(len(tokens), MAX_SOURCE_TOKENS) + 1):
            if size < len(tokens):
                children[" ".join(tokens[:size])].add(tokens[size])

    sources: set[str] = set()
    for tokens in tokenized:
        limit = min(len(tokens), MAX_SOURCE_TOKENS)
        chosen = ""
        for size in range(1, limit + 1):
            prefix = " ".join(tokens[:size])
            # Stop where the continuation becomes unpredictable, or at the
            # last token that can still leave an identifier behind.
            if len(children[prefix]) >= min_branch or size == len(tokens) - 1:
                # ...unless what follows is the standard's own number, in
                # which case the name is not finished yet.
                if size < len(tokens) and _STANDARD_NUMBER_RE.match(tokens[size]):
                    continue
                chosen = prefix
                break
        candidate = (chosen or " ".join(tokens[:1])).strip(_SOURCE_TRIM)
        if candidate:
            sources.add(candidate)
    return sources


def split_mapping(line: str, sources: set[str]) -> tuple[str, str]:
    """Split one mapping line into `(source, identifier)`.

    Longest match wins, so `NIST SP 800-53 r5` beats `NIST`. A line no
    learned source matches is returned whole as the identifier under an
    empty source rather than guessed at -- a mapping filed under the wrong
    framework is worse than one filed under none.
    """
    text = " ".join((line or "").split())
    best = ""
    for source in sources:
        if len(source) <= len(best) or not text.startswith(source):
            continue
        # A source only matches on a token boundary, so `NIST SP 800-53 r5`
        # never matches a line that merely begins `NIST SP 800-53 r53`.
        if len(text) == len(source) or not text[len(source)].isalnum():
            best = source
    if not best:
        return "", text
    return best, text[len(best) :].strip(_SOURCE_TRIM).strip()


def build_mappings(cell: str, sources: set[str]) -> dict[str, list[str]]:
    """`{source: [identifier, ...]}` for one requirement's mapping cell."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for line in split_lines(cell):
        source, identifier = split_mapping(line, sources)
        if identifier and identifier not in grouped[source]:
            grouped[source].append(identifier)
    return dict(grouped)


@dataclass
class Record:
    """One (control reference x level) row, with its fields already named.

    The intermediate every format lands on. A CSV loader, an HTML loader and
    an LLM-generated loader for some export nobody has seen yet all produce
    these, and `build_controls` is the only place that knows how a Control
    is assembled out of them.
    """

    category: str = ""
    objective: str = ""
    objective_statement: str = ""
    reference: str = ""
    specification: str = ""
    factor_type: str = ""
    level: str = ""
    statement: str = ""
    organizational_factors: str = ""
    system_factors: str = ""
    regulatory_factors: str = ""
    mapping: str = ""

    def key(self) -> tuple[str, str]:
        return (" ".join(self.reference.split()), " ".join(self.level.split()))


def dedupe(records: list[Record]) -> list[Record]:
    """Collapse the report's repeated rows, keeping one per reference+level.

    A report export can repeat whole rows, because the report joins against
    tables it does not render. The
    duplicates are byte-identical, so keeping one loses nothing. Where two
    rows share a key but differ, the longer statement wins, on the grounds
    that the shorter one is the truncated render.
    """
    kept: dict[tuple[str, str], Record] = {}
    for record in records:
        if not record.reference or not record.level:
            continue
        existing = kept.get(record.key())
        if existing is None or len(record.statement) > len(existing.statement):
            kept[record.key()] = record
    return list(kept.values())


def build_controls(
    records: list[Record],
    *,
    version: str = "",
    source_path: str | None = None,
) -> list[Control]:
    """Assemble deduplicated records into one `Control` per control reference.

    Requirements are ordered maturity-first and then by label, so Level
    1/2/3 read as the ladder they are and the overlays follow in a stable
    order rather than in whatever order the report emitted them.
    """
    records = dedupe(records)
    sources = learn_sources([line for r in records for line in split_lines(r.mapping)])
    full_level = merge_level_names([r.level for r in records])

    grouped: dict[str, list[Record]] = defaultdict(list)
    order: list[str] = []
    for record in records:
        reference_id, _ = parse_reference(record.reference)
        key = reference_id or " ".join(record.reference.split())
        if key not in grouped:
            order.append(key)
        grouped[key].append(record)

    controls: list[Control] = []
    for key in order:
        rows = grouped[key]
        head = rows[0]
        reference_id, reference_title = parse_reference(head.reference)
        category_id, category_title = parse_category(head.category)
        objective_id, objective_title = parse_objective(head.objective)

        requirements = []
        by_id: dict[str, tuple[Record, Requirement]] = {}
        for row in rows:
            label = full_level.get(" ".join(row.level.split()), row.level)
            label, kind = parse_level(label)
            requirement = Requirement(
                requirement_id=f"{reference_id or key} {label}".strip(),
                level=label,
                level_kind=kind,
                statement=" ".join(row.statement.split()),
                organizational_factors=split_lines(row.organizational_factors),
                system_factors=split_lines(row.system_factors),
                regulatory_factors=split_lines(row.regulatory_factors),
                mappings=build_mappings(row.mapping, sources),
            )
            earlier = by_id.get(requirement.requirement_id)
            if earlier is None:
                by_id[requirement.requirement_id] = (row, requirement)
                requirements.append(requirement)
                continue
            # Conservation (#15): one id, one requirement. Two rows reaching the
            # same id are one requirement rendered twice only if they agree, a
            # statement cut short aside; otherwise one would overwrite the other
            # wherever requirements are keyed by id.
            kept = _same_requirement(earlier[1], requirement)
            if kept is None:
                raise HitrustBuildError(
                    f"{reference_id or key}: two different requirements would share the id "
                    f"{requirement.requirement_id!r}, from the level labels "
                    f"{' '.join(earlier[0].level.split())!r} and "
                    f"{' '.join(row.level.split())!r}. Refusing rather than merging them."
                )
            requirements[requirements.index(earlier[1])] = kept
            by_id[requirement.requirement_id] = (row, kept)
        requirements.sort(key=_requirement_order)

        controls.append(
            Control(
                control_id=reference_id or key,
                title=reference_title,
                framework=FRAMEWORK,
                framework_version=version,
                family=category_title or None,
                family_abbr=category_id or None,
                objective_id=objective_id,
                objective_title=objective_title,
                objective_statement=" ".join(head.objective_statement.split()),
                factor_type=" ".join(head.factor_type.split()),
                control_statement=" ".join(head.specification.split()),
                # The levels this control is stated at. The closest thing
                # HITRUST has to 800-53's low/moderate/high, and what the
                # pipeline's baseline filters read.
                baseline=", ".join(r.level for r in requirements) or None,
                requirements=requirements,
                source_path=source_path,
            )
        )
    return controls


def _same_requirement(a: Requirement, b: Requirement) -> Requirement | None:
    """The fuller of two renders of one requirement, or None if they differ.

    A statement that is the start of the other is the truncated render, and
    everything else must match. Stricter than `dedupe`, which keeps the
    longer of two rows with one key whatever their text (#15, recorded
    there): here the two rows came from different level labels, so the
    labels are the only evidence they are one requirement."""
    if (
        a.organizational_factors,
        a.system_factors,
        a.regulatory_factors,
        a.mappings,
    ) != (b.organizational_factors, b.system_factors, b.regulatory_factors, b.mappings):
        return None
    if b.statement.startswith(a.statement):
        return b
    if a.statement.startswith(b.statement):
        return a
    return None


def _requirement_order(requirement: Requirement) -> tuple[int, str]:
    if requirement.level_kind == MATURITY:
        return (0, requirement.level)
    return (1, requirement.level)


@dataclass
class Summary:
    """What a parse produced, and whether it looks like a whole library."""

    categories: int = 0
    objectives: int = 0
    references: int = 0
    requirements: int = 0
    maturity_levels: int = 0
    overlay_levels: int = 0
    sources: int = 0
    mapped_identifiers: int = 0
    levels: Counter = field(default_factory=Counter)
    top_sources: list[tuple[str, int]] = field(default_factory=list)
    #: (level label, built requirements) for every level label left at the
    #: report's 36-character level-name width, with no longer form anywhere in
    #: the export's level cells to rejoin it to (#27). Whether each was cut
    #: can't be told from the export: a real name can be exactly that long.
    at_cut_width: list[tuple[str, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def format_report(self) -> str:
        lines = [
            f"HITRUST CSF: {self.references} control references, "
            f"{self.requirements} requirement statements",
            f"  {self.categories} categories, {self.objectives} objectives",
            f"  {self.maturity_levels} maturity levels, {self.overlay_levels} overlays",
            f"  {self.mapped_identifiers} mapped identifiers across {self.sources} sources",
        ]
        if self.top_sources:
            lines.append("")
            lines.append("  most-mapped authoritative sources:")
            width = min(max(len(s) for s, _ in self.top_sources), 46)
            for source, count in self.top_sources:
                lines.append(f"    {source[:46].ljust(width)}  {count}")
        if self.warnings:
            lines.append("")
            for warning in self.warnings:
                lines.append(f"  warn  {warning}")
        return "\n".join(lines)


def summarize(
    controls: list[Control], *, top: int = 12, library_counts: dict[str, int] | None = None
) -> Summary:
    """Describe a parsed catalog, and say when it looks incomplete.

    Worth running after every parse. The failure mode of a heuristic loader
    is not an exception but a plausible-looking catalog with a third of the
    library missing, and only the counts show that.
    """
    summary = Summary()
    summary.references = len(controls)
    summary.categories = len({c.family_abbr for c in controls if c.family_abbr})
    summary.objectives = len({c.objective_id for c in controls if c.objective_id})

    source_counts: Counter = Counter()
    kinds: Counter = Counter()
    for control in controls:
        for requirement in control.requirements:
            summary.requirements += 1
            summary.levels[requirement.level] += 1
            kinds[requirement.level_kind] += 1
            for source, identifiers in requirement.mappings.items():
                summary.mapped_identifiers += len(identifiers)
                source_counts[source or "(unrecognised)"] += len(identifiers)

    summary.maturity_levels = len(
        {r.level for c in controls for r in c.requirements if r.level_kind == MATURITY}
    )
    summary.overlay_levels = len(
        {r.level for c in controls for r in c.requirements if r.level_kind == OVERLAY}
    )
    summary.sources = len(source_counts)
    summary.top_sources = source_counts.most_common(top)
    # After the rejoin, so a label that found its full name is not here. Said,
    # never guessed: 80 on #27 (comment 400), and 1d (comment 408): nothing
    # may call these clipped or whole, only at the cut width.
    summary.at_cut_width = sorted(
        (label, count) for label, count in summary.levels.items() if level_may_be_truncated(label)
    )
    if summary.at_cut_width:
        named = "; ".join(f"{label!r} ({count})" for label, count in summary.at_cut_width)
        summary.warnings.append(
            f"{len(summary.at_cut_width)} level label(s) are at the report's "
            f"{LEVEL_TRUNCATION}-character level-name width (35 where the cut fell after a "
            "space), and no longer form of them is in this export, so each is kept as written. "
            "A name that long can't be told apart from a cut one. Label (requirements): " + named
        )

    for tier, expected in (library_counts or {}).items():
        actual = getattr(summary, tier)
        if actual and actual < expected * 0.6:
            summary.warnings.append(
                f"{actual} {tier} parsed, against {expected} in a full "
                "library -- this looks like a partial export or a missed column."
            )
    if summary.requirements and not summary.mapped_identifiers:
        summary.warnings.append(
            "no authoritative-source mappings parsed, so no crosswalk can be "
            "built from this export -- check that the mapping column was found."
        )
    if kinds and not kinds.get(MATURITY):
        summary.warnings.append(
            "no Level 1/2/3 requirements found, which every control reference "
            "should have -- the level column may have been misidentified."
        )
    return summary

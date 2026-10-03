"""One decided value per organization-defined parameter, and why.

SP 800-53 Rev 5 does not tell you how often to review accounts. It says
`[Assignment: organization-defined frequency]` and leaves the number to you
— 1,210 times across the catalog this project ships, counting statements and
enhancements. Every one of those is a decision somebody has to make and
defend, and today they get made implicitly, inside generated prose, by a
model that has no memory of what it chose for the neighbouring control.

That produces documents that are individually plausible and collectively
indefensible. The Access Control Standard says quarterly, the Audit
Standard says "periodically", the SSP says annually, and nobody decided
anything — three drafts did. An assessor asking "why quarterly?" gets no
answer, because there isn't one.

The ledger makes each value a *recorded decision*:

    AC-2/frequency:
      value: quarterly
      rationale: >-
        HITRUST 01.c specifies quarterly for account review; 800-53 leaves it
        organization-defined, so the stricter framework governs.
      source: HITRUST CSF 01.c

Three properties follow, and they are the whole point:

* **One decision, many documents.** The value is substituted into control
  text before synthesis, so every document drawn from that control says the
  same thing, and so does the SSP. Consistency stops being something to
  check for and becomes something that cannot fail.
* **The reasoning survives.** "Why quarterly" is the question asked a year
  later, by an assessor or by whoever inherits the programme, and the answer
  belongs next to the value rather than in someone's memory.
* **Undecided is visible.** A parameter with no decision stays as
  `[Assignment: organization-defined frequency]` in the output, which reads
  as the gap it is. That is better than a number nobody chose.

**On keys.** A parameter is identified by its control and its label —
`AC-2/frequency`, `AU-6/personnel-or-roles` — rather than by the OSCAL
identifier (`ac-02_odp.01`) that NIST assigns. The OSCAL ids are more
durable across rewordings and this project's loader currently discards them,
but the deciding factor is who reads this file: a human filling in a hundred
values needs to see which control and which quantity, and `ac-02_odp.04`
tells them neither. The durability cost is handled rather than ignored — a
key that no longer matches any parameter is reported as stale rather than
silently dropped, so a catalog rewording surfaces as a question instead of a
lost decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LEDGER_PATH = Path("config/parameters.yaml")

ASSIGNMENT = "assignment"
SELECTION = "selection"

#: `[Assignment: organization-defined frequency]` and
#: `[Selection (one or more): organization-level; system-level]`. Neither
#: form nests brackets in the published catalog, so a non-greedy match to
#: the first `]` is exact rather than approximate.
_ODP_RE = re.compile(r"\[(Assignment|Selection)([^\]]*)\]")

_LABEL_PREFIX_RE = re.compile(r"^\s*:?\s*organization-defined\s+", re.IGNORECASE)


@dataclass
class Parameter:
    """One organization-defined value the catalog leaves to you."""

    key: str
    control_id: str
    label: str
    kind: str = ASSIGNMENT
    #: For a Selection, the options the catalog offers. Recorded so the
    #: ledger can say what a legal answer looks like rather than leaving
    #: somebody to go and read the control.
    choices: list[str] = field(default_factory=list)
    #: The literal `[Assignment: ...]` text, which is what gets replaced.
    marker: str = ""
    #: How many times this marker appears in this control. A frequency used
    #: three times in one control is one decision, not three.
    occurrences: int = 1
    #: Enough surrounding words to see what is being decided.
    context: str = ""
    #: The catalog the parameter is from, as it declares itself.
    framework: str = ""

    @property
    def is_selection(self) -> bool:
        return self.kind == SELECTION


@dataclass
class Decision:
    """A value somebody chose, and the reasoning that stands behind it."""

    value: str = ""
    rationale: str = ""
    source: str = ""

    @property
    def decided(self) -> bool:
        return bool(self.value.strip())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _label_of(kind: str, body: str) -> str:
    """The human name of what is being decided."""
    if kind == SELECTION:
        return "selection"
    label = _LABEL_PREFIX_RE.sub("", body).strip(" :")
    # Some labels carry a parenthetical qualifier — "attributes (as
    # required)" — which is noise in a key and useful in the label.
    return label or "parameter"


def _choices_of(body: str) -> list[str]:
    _, _, rest = body.partition(":")
    return [choice.strip() for choice in rest.split(";") if choice.strip()]


def extract_parameters(controls) -> list[Parameter]:
    """Every organization-defined parameter in a set of controls.

    Enhancements are searched alongside the control statement, since an
    enhancement's parameters are as organization-defined as its parent's and
    an SSP has to answer for both.

    Repeated markers within one control collapse into a single parameter. A
    control that says "frequency" three times is asking one question three
    times, and a ledger that made you answer it three times would be one
    people abandon.
    """
    found: dict[str, Parameter] = {}

    for control in controls:
        statement = getattr(control, "control_statement", "") or ""
        blocks = [statement]
        blocks += [e.description or "" for e in getattr(control, "enhancements", [])]
        text = "\n".join(blocks)

        seen_labels: dict[str, int] = {}
        for match in _ODP_RE.finditer(text):
            kind = SELECTION if match.group(1) == "Selection" else ASSIGNMENT
            body = match.group(2)
            label = _label_of(kind, body)
            base = f"{control.control_id}/{_slug(label)}"

            existing = found.get(base)
            if existing is not None and existing.marker == match.group(0):
                existing.occurrences += 1
                continue

            # Two different parameters that slug alike get an ordinal. Rare,
            # and better than silently merging two distinct decisions.
            seen_labels[base] = seen_labels.get(base, 0) + 1
            key = base if seen_labels[base] == 1 else f"{base}.{seen_labels[base]}"
            if key in found:
                found[key].occurrences += 1
                continue

            # Start the snippet at a word boundary; a context that opens
            # mid-word ("res; and c. Review...") reads as corruption.
            start = max(0, match.start() - 70)
            snippet = text[start : match.end() + 40]
            if start > 0 and " " in snippet:
                snippet = snippet.split(" ", 1)[1]
            found[key] = Parameter(
                key=key,
                control_id=control.control_id,
                label=label,
                kind=kind,
                choices=_choices_of(body) if kind == SELECTION else [],
                marker=match.group(0),
                context=" ".join(snippet.split()),
                framework=getattr(control, "framework", "") or "",
            )

    return list(found.values())


def _scalar(text: str) -> str:
    """Quote a value as a YAML scalar.

        Not `yaml.safe_dump`: on a bare string that emits a document-end marker
        (`x
    ...
    `), and stripping it leaves `...` sitting inside the mapping,
        which corrupts the file it was meant to write. Newlines are folded to
        spaces so a multi-sentence rationale stays one valid scalar.
    """
    folded = " ".join(str(text).split())
    if not folded:
        return '""'
    return "'" + folded.replace("'", "''") + "'"


def load_ledger(path: Path = DEFAULT_LEDGER_PATH) -> dict[str, Decision]:
    """Read decisions from disk. A missing ledger is an empty one."""
    import yaml

    if not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = data.get("parameters") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}

    decisions: dict[str, Decision] = {}
    for key, entry in raw.items():
        if isinstance(entry, dict):
            decisions[str(key)] = Decision(
                value=str(entry.get("value") or "").strip(),
                rationale=str(entry.get("rationale") or "").strip(),
                source=str(entry.get("source") or "").strip(),
            )
        elif entry is not None:
            # A bare `AC-2/frequency: quarterly` is what people write first.
            # Accept it; the report will say the reasoning is missing.
            decisions[str(key)] = Decision(value=str(entry).strip())
    return decisions


def _from(parameter: Parameter) -> str:
    """` (from NIST 800-171)` for a parameter from any catalog but 800-53.

    A topic reaches a catalog it does not anchor through the crosswalk, so
    the ledger can hold rows from 800-171 beside 800-53's (80's ruling on
    #377). They look alike, and their ids do not say which catalog asked:
    `03.01.01` could be any NIST catalog's. So each says where it came from.
    """
    from policyforge.mapping.crosswalk import NIST_ANCHOR, normalize_framework

    if not parameter.framework or normalize_framework(parameter.framework) == NIST_ANCHOR:
        return ""
    return f" (from {parameter.framework})"


def render_ledger(parameters: list[Parameter], decisions: dict[str, Decision]) -> str:
    """Write a ledger, keeping decisions already made and adding the rest."""

    lines = [
        "# Organization-defined parameters: one decided value each, and why.",
        "#",
        "# SP 800-53 does not say how often to review accounts — it says",
        "# [Assignment: organization-defined frequency] and leaves it to you.",
        "# A value decided here is substituted into the control text before",
        "# synthesis, so every document drawn from that control says the same",
        "# thing, and so does the SSP.",
        "#",
        "# A parameter left blank stays as [Assignment: ...] in the output,",
        "# which reads as the gap it is. That is better than a number nobody",
        "# chose. Fill in `rationale` and `source` too: 'why quarterly' is the",
        "# question asked a year later, and the answer belongs next to the",
        "# value rather than in somebody's memory.",
        "#",
        "# Regenerate with `policyforge parameters --init`; existing decisions",
        "# are preserved.",
        "",
        "parameters:",
    ]

    for parameter in sorted(parameters, key=lambda p: (p.control_id, p.key)):
        decision = decisions.get(parameter.key, Decision())
        lines.append("")
        lines.append(f"  # {parameter.control_id}{_from(parameter)}: {parameter.label}")
        if parameter.choices:
            lines.append(f"  #   one of: {'; '.join(parameter.choices)}")
        if parameter.context:
            lines.append(f"  #   {parameter.context}")
        lines.append(f"  {parameter.key}:")
        for name in ("value", "rationale", "source"):
            text = getattr(decision, name)
            lines.append(f"    {name}: {_scalar(text)}")

    # Decisions whose parameter is no longer in the catalog are kept rather
    # than dropped. A rewording upstream should cost somebody a question,
    # not a decision they made and defended.
    known = {p.key for p in parameters}
    orphans = {k: d for k, d in decisions.items() if k not in known and d.decided}
    if orphans:
        lines += [
            "",
            "  # These no longer match any parameter in the loaded catalog —",
            "  # a control was reworded, or the catalog changed version.",
            "  # Kept so the decision is not lost. Re-key or delete them.",
        ]
        for key, decision in sorted(orphans.items()):
            lines.append(f"  {key}:")
            for name in ("value", "rationale", "source"):
                text = getattr(decision, name)
                lines.append(f"    {name}: {_scalar(text)}")

    return "\n".join(lines) + "\n"


def resolve_text(text: str, control_id: str, parameters, decisions) -> tuple[str, int]:
    """Substitute decided values into one control's text.

    Returns `(text, substitutions made)`. Markers with no decision are left
    exactly as written — an undecided parameter should read as undecided all
    the way through to the document.
    """
    for parameter in parameters:
        if parameter.control_id != control_id:
            continue
        decision = decisions.get(parameter.key)
        if decision is None or not decision.decided:
            continue
        text = text.replace(parameter.marker, decision.value)
    return text, 0


def apply_to_controls(controls, decisions: dict[str, Decision]) -> tuple[list, int]:
    """Return controls with decided parameter values substituted in.

    Copies rather than mutates: the caller's control objects are shared with
    everything else in a run, and a pipeline stage that quietly rewrote them
    would make the substitution's blast radius impossible to reason about.
    """
    import copy

    parameters = extract_parameters(controls)
    by_control: dict[str, list[Parameter]] = {}
    for parameter in parameters:
        by_control.setdefault(parameter.control_id, []).append(parameter)

    filled = 0
    resolved = []
    for control in controls:
        clone = copy.deepcopy(control)
        for parameter in by_control.get(clone.control_id, []):
            decision = decisions.get(parameter.key)
            if decision is None or not decision.decided:
                continue
            if parameter.marker in (clone.control_statement or ""):
                clone.control_statement = clone.control_statement.replace(
                    parameter.marker, decision.value
                )
                filled += 1
            for enhancement in clone.enhancements:
                if parameter.marker in (enhancement.description or ""):
                    enhancement.description = enhancement.description.replace(
                        parameter.marker, decision.value
                    )
                    filled += 1
        resolved.append(clone)
    return resolved, filled


@dataclass
class LedgerReport:
    parameters: list[Parameter] = field(default_factory=list)
    decisions: dict[str, Decision] = field(default_factory=dict)

    @property
    def decided(self) -> list[Parameter]:
        return [p for p in self.parameters if self.decisions.get(p.key, Decision()).decided]

    @property
    def undecided(self) -> list[Parameter]:
        return [p for p in self.parameters if not self.decisions.get(p.key, Decision()).decided]

    @property
    def unreasoned(self) -> list[Parameter]:
        """Decided, but with no rationale recorded — the answer without the why."""
        return [
            p
            for p in self.decided
            if not self.decisions[p.key].rationale and not self.decisions[p.key].source
        ]

    @property
    def stale(self) -> list[str]:
        known = {p.key for p in self.parameters}
        return sorted(k for k, d in self.decisions.items() if k not in known and d.decided)

    def by_label(self) -> list[tuple[str, int, int]]:
        """(label, total, decided) per kind of value, commonest first.

        The view that makes a thousand parameters tractable. They are not a
        thousand different questions — "frequency" is asked a hundred times
        and "personnel or roles" sixty, and deciding one kind of value at a
        sitting is how a person actually works through this. Ordering by
        count puts the decisions with the most leverage at the top.
        """
        totals: dict[str, int] = {}
        done: dict[str, int] = {}
        for parameter in self.parameters:
            totals[parameter.label] = totals.get(parameter.label, 0) + 1
            if self.decisions.get(parameter.key, Decision()).decided:
                done[parameter.label] = done.get(parameter.label, 0) + 1
        return sorted(
            ((label, count, done.get(label, 0)) for label, count in totals.items()),
            key=lambda row: (-row[1], row[0]),
        )

    def format_report(self, limit: int = 20, *, group: bool = False) -> str:
        total = len(self.parameters)
        lines = [
            f"{total} organization-defined parameter(s) in scope: "
            f"{len(self.decided)} decided, {len(self.undecided)} undecided"
        ]

        if group and self.parameters:
            lines += ["", "By the kind of value being decided:"]
            rows = self.by_label()
            width = min(max(len(label) for label, _, _ in rows), 34)
            for label, count, done in rows[:limit]:
                lines.append(f"  {label.ljust(width)}  {done}/{count} decided")
            if len(rows) > limit:
                lines.append(f"  ... and {len(rows) - limit} more kinds")
        elif self.undecided:
            lines += ["", "Undecided:"]
            width = min(max(len(p.key) for p in self.undecided), 46)
            for parameter in self.undecided[:limit]:
                note = f"one of: {'; '.join(parameter.choices)}" if parameter.choices else ""
                lines.append(f"  {parameter.key.ljust(width)}  {note or parameter.label}")
            if len(self.undecided) > limit:
                lines.append(f"  ... and {len(self.undecided) - limit} more")

        if self.unreasoned:
            lines += [
                "",
                f"{len(self.unreasoned)} decided with no rationale or source. "
                "'Why quarterly' is the question asked a year later:",
            ]
            lines += [f"  {p.key}" for p in self.unreasoned[:limit]]

        if self.stale:
            lines += [
                "",
                f"{len(self.stale)} decision(s) match no parameter in the loaded "
                "catalog — reworded, or a different catalog version:",
            ]
            lines += [f"  {key}" for key in self.stale[:limit]]

        return "\n".join(lines)


def build_report(controls, decisions: dict[str, Decision]) -> LedgerReport:
    return LedgerReport(parameters=extract_parameters(controls), decisions=decisions)

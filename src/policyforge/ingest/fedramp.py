"""What FedRAMP publishes now, and what it stopped publishing.

FedRAMP writes no control catalog of its own. Like GovRAMP it is a **profile
over NIST SP 800-53 Rev 5**: it names controls NIST already wrote, and adds
the two things the base catalog deliberately leaves open — the
organization-defined values FedRAMP has already decided, and the normative
guidance it layers on top. Those two are the whole reason to ingest a
profile, which is why `Control` keeps `parameter_values` and
`additional_requirements` apart from `control_statement`.

## This catalog is not a baseline, and must not be read as one

The important caveat, stated first because getting it wrong would misstate
somebody's scope:

**There is no FedRAMP baseline in here.** Nothing in this catalog says which
controls a Low, Moderate or High system must implement.

Until recently that selection was published as OSCAL profiles in
`GSA/fedramp-automation`, which is where a loader like this one would have
read it. That repository is gone — not archived, not moved: the URL and the
GitHub API both return 404. No official machine-readable replacement for the
Rev5 Low/Moderate/High baseline selection has been published in its place,
and a baseline reassembled from an unofficial mirror would be worse than no
baseline at all. This project's value is that a citation traces to the body
that issued it; a mirror traces to whoever made the copy.

So `Control.baseline` is left unset by this loader, on purpose. An empty
field reads as "unknown" to `ssp --baseline` and to `coverage`. A fabricated
one would read as an answer.

What FedRAMP does still publish, and what this reads, is the
**tailoring**: `fedramp-consolidated-rules.json` in `FedRAMP/rules`, which
that repository's own README calls the canonical rules dataset. Its `CTL`
section carries FedRAMP's parameter values and guidance for 79 controls.
That is a real, authoritative, useful thing — it is simply a smaller thing
than a baseline, and the summary this module returns says so in as many
words rather than letting a count of 79 be mistaken for a control selection.

## Why this loader needs the 800-53 catalog

The rules dataset carries no control text and no titles: `AC-06-01` is a
bare key whose entry is two parameter values. FedRAMP's own website renders
these pages by joining the same dataset onto the NIST catalog, and this does
what the publisher does. Without the join a generated document would cite
`AC-6(1)` and have nothing to say about it.

The join is exact — all 79 ids resolve against the bundled Rev 5 catalog,
which is itself at 5.2.0, the revision FedRAMP's control pages name. Ids are
normalized on the way in: FedRAMP writes `AC-06-01`, zero-padded and
dash-separated, where this codebase and OSCAL write `AC-6(1)`. The
identifier *is* the join key between a profile and the catalog it profiles,
so a catalog written in the source's own spelling would crosswalk to
nothing.

## Certification classes are not impact levels

Two controls (IA-5 and SA-9(5)) carry `varies_by_class`, whose keys are `b`,
`c` and `d`. These are FedRAMP **Certification Classes** — "the category of
assurance that a cloud service offering supplies to federal government
customers … increasing from minimal assurance at Class A to significant
assurance at Class D". They are the 2026 successor to the Low/Moderate/High
axis, and they are not the same thing: a class is an assurance category, not
a FIPS 199 categorisation of the system.

They are also not a selection. `varies_by_class` says this control's
guidance and parameter values read differently depending on the class
pursued; it does not say the control is required at one class and not
another. IA-5 varies only its guidance — the NIST Digital Identity
Guidelines level rises with the class — while SA-9(5) varies its parameter
values, deciding `sa-09.05_odp.02` for Class D and leaving it open for Class
C. Rendering both under a heading that names the class keeps the two facts
visible at once: an assessor reads the line that applies to them, and nobody
mistakes the presence of three classes for a three-tier baseline.
"""

from __future__ import annotations

import re

from .schema import Control, ControlEnhancement

#: The upstream revision this tailoring is read from.
#:
#: `FedRAMP/rules` publishes no tags and cuts no releases, so unlike the
#: OSCAL catalog there is no published name for a revision — a commit is the
#: only thing that identifies one. This particular commit is not an
#: arbitrary pick of `main`: it is the sha recorded in `FedRAMP/2026-markdown`'s
#: `_sources.json` as the revision FedRAMP built its own published content
#: from, which makes it the closest thing the dataset has to a release.
#:
#: Verified before pinning: the file at this commit and at `main` were byte
#: identical (sha256 64915d88e723…), so pinning changed nothing about the
#: content — only about whether it can be named.
#:
#: Moving this is a deliberate act: bump the ref, re-run `etl-fedramp`, and
#: let the diff on `controls.json` be reviewed.
FEDRAMP_RULES_REF = "58487bda77d76d9ce334304ec2e779ece7cc7d54"

RULES_URL = (
    f"https://raw.githubusercontent.com/FedRAMP/rules/{FEDRAMP_RULES_REF}"
    f"/fedramp-consolidated-rules.json"
)

#: `AC-20`, `AC-06-01` — the shape the dataset's own schema pins its control
#: keys to (`^[A-Z]{2}-\d{2}(-\d{2})?$`).
_CTL_KEY_RE = re.compile(r"^([A-Z]{2})-(\d{2})(?:-(\d{2}))?$")

#: Certification class keys, ordered least to most assurance, so rendered
#: guidance reads b, c, d rather than in dict order.
CLASS_ORDER = ("a", "b", "c", "d")

#: The base catalog FedRAMP profiles, used as the crosswalk key so
#: `mapping/crosswalk.py` anchors every FedRAMP control on its 800-53
#: equivalent — which, FedRAMP being a profile, is the same identifier. Same
#: convention and same key as `govramp.py`, for the same reason.
#:
#: Recorded only for ids that actually resolve in the catalog passed in.
#: Identity is the obvious mapping and it is still an assertion: if a later
#: revision of the dataset names a control this 800-53 catalog does not have,
#: the honest result is no crosswalk entry and a line in the summary, not a
#: mapping onto a control that isn't there.
NIST_SOURCE = "NIST 800-53"


def normalize_control_id(key: str) -> tuple[str, str]:
    """`("AC-20", "")` for a control, `("AC-6", "AC-6(1)")` for an enhancement.

    The second element is the enhancement's own identifier, empty when the
    key names a base control. Both are normalized to the spelling the OSCAL
    loader produces, for the reason given in the module docstring: the id is
    the join key.

    An unrecognized key is returned verbatim as a base id rather than
    dropped. A key that reached here is a real entry in FedRAMP's dataset,
    and a tailored control missing from a scope report is worse than one
    with an odd identifier — `parse_fedramp_rules` reports these so they are
    visible either way.
    """
    match = _CTL_KEY_RE.match((key or "").strip())
    if match is None:
        return " ".join((key or "").split()), ""
    family, number, enhancement = match.groups()
    base = f"{family.upper()}-{int(number)}"
    return (base, f"{base}({int(enhancement)})") if enhancement else (base, "")


def _render_guidance(entry: dict) -> str:
    """FedRAMP's guidance for one control, as one block of normative prose.

    Uniform guidance comes first and unlabelled; per-class guidance follows
    under a heading naming the class, because a reader who needs Class C has
    to be able to tell which sentences are theirs. Verbatim and
    line-preserved: these are sentences an assessor reads, not a summary.
    """
    blocks: list[str] = []
    blocks.extend(str(line).strip() for line in entry.get("guidance", []) if str(line).strip())

    varies = entry.get("varies_by_class") or {}
    for key in CLASS_ORDER:
        per_class = varies.get(key)
        if not isinstance(per_class, dict):
            continue
        lines = [str(line).strip() for line in per_class.get("guidance", []) if str(line).strip()]
        if lines:
            blocks.append(f"Certification Class {key.upper()}:\n" + "\n".join(lines))

    return "\n\n".join(blocks)


def _render_parameters(entry: dict) -> dict[str, str]:
    """`{"ac-06.01_odp.02": "all functions not publicly accessible"}`.

    Keyed by the OSCAL parameter id FedRAMP writes them against, which is
    the same id the 800-53 prose carries in its `{{ insert: param, ... }}`
    placeholders — so a consumer can put the decided value where the
    assignment is, rather than beside it.

    Per-class values are folded in under the same keys with the class named
    in the value, since one parameter genuinely resolves two ways: SA-9(5)
    decides `sa-09.05_odp.02` for Class D and leaves it open for Class C.

    Classes that agree are named together — "Certification Classes C, D:"
    rather than the same sentence twice — because SA-9(5)'s other two values
    are identical across both, and a reader scanning for a difference should
    not have to compare two lines to find there isn't one.
    """
    values: dict[str, str] = {}
    for param in entry.get("parameters", []):
        pid, value = param.get("parameterId"), param.get("value")
        if pid and value:
            values[str(pid)] = str(value).strip()

    # parameter id -> value -> the classes that decided it that way, kept in
    # CLASS_ORDER so the label reads "C, D" rather than in encounter order.
    by_value: dict[str, dict[str, list[str]]] = {}
    varies = entry.get("varies_by_class") or {}
    for key in CLASS_ORDER:
        per_class = varies.get(key)
        if not isinstance(per_class, dict):
            continue
        for param in per_class.get("parameters", []):
            pid, value = param.get("parameterId"), param.get("value")
            if not pid or not value:
                continue
            by_value.setdefault(str(pid), {}).setdefault(str(value).strip(), []).append(key.upper())

    for pid, grouped in by_value.items():
        lines = [
            f"Certification Class{'es' if len(classes) > 1 else ''} {', '.join(classes)}: {value}"
            for value, classes in grouped.items()
        ]
        existing = values.get(pid)
        values[pid] = "\n".join([existing, *lines]) if existing else "\n".join(lines)

    return values


def index_catalog(controls: list[Control]) -> dict[str, object]:
    """Every 800-53 control and enhancement by its identifier.

    Flat across both tiers because FedRAMP tailors both, and a caller
    resolving `AC-6(1)` should not have to know whether that is a control or
    an enhancement to find its text.
    """
    index: dict[str, object] = {}
    for control in controls:
        index[control.control_id] = control
        for enhancement in control.enhancements:
            index[enhancement.enhancement_id] = enhancement
    return index


class Summary:
    """What a run found, in the terms the caller needs to report honestly.

    `tailored` is the number this loader is about. It is deliberately not
    called anything resembling "controls in the baseline", because the one
    mistake this whole module is arranged to prevent is a reader taking it
    for a control selection.
    """

    def __init__(self) -> None:
        self.tailored = 0
        self.parameters = 0
        self.with_parameters = 0
        self.with_guidance = 0
        self.varies_by_class: list[str] = []
        self.unresolved: list[str] = []
        self.unparsed: list[str] = []

    def format_report(self) -> list[str]:
        lines = [
            f"Tailored {self.tailored} controls "
            f"({self.with_parameters} with parameter values, "
            f"{self.with_guidance} with guidance).",
            f"Recorded {self.parameters} FedRAMP-decided parameter values.",
        ]
        if self.varies_by_class:
            lines.append(
                f"Guidance varies by certification class on {', '.join(self.varies_by_class)}."
            )
        if self.unresolved:
            shown = ", ".join(self.unresolved[:8])
            more = f" (+{len(self.unresolved) - 8} more)" if len(self.unresolved) > 8 else ""
            lines.append(f"Not found in the 800-53 catalog: {shown}{more}.")
        if self.unparsed:
            lines.append(f"Unrecognized control keys kept verbatim: {', '.join(self.unparsed)}.")
        lines.append(
            "No baseline selection: FedRAMP publishes none in machine-readable "
            "form since GSA/fedramp-automation was withdrawn, so these carry "
            "tailoring only and Control.baseline is left unset."
        )
        return lines


def parse_fedramp_rules(rules: dict, nist_controls: list[Control]) -> tuple[list[Control], Summary]:
    """FedRAMP's `CTL` tailoring, joined onto the 800-53 text it tailors.

    Returns controls sorted by family then number, and a `Summary` whose
    report is written to be read aloud in a review.

    A base control is emitted whenever FedRAMP tailors it *or* any of its
    enhancements, because an enhancement has nowhere else to live in this
    schema — `Control.enhancements` is the only container there is. A parent
    emitted only to carry a tailored child holds the NIST text and no
    FedRAMP tailoring of its own, which is the truth about it: FedRAMP says
    nothing about AC-6 while saying three things about its enhancements.
    """
    index = index_catalog(nist_controls)
    version = str((rules.get("info") or {}).get("version") or "").strip()
    summary = Summary()

    # base id -> the Control being assembled, so enhancements of the same
    # parent land on one object however the source happens to order them.
    assembled: dict[str, Control] = {}

    # Counted in a pass of its own, before the loop that consumes them.
    # Incrementing alongside the parse would make the comparison below a
    # tautology: a loop that stopped early would move both sides together.
    declared = sum(len(family) for family in (rules.get("CTL") or {}).values())
    not_a_mapping = 0

    for family_controls in (rules.get("CTL") or {}).values():
        for key, entry in sorted(family_controls.items()):
            if not isinstance(entry, dict):
                not_a_mapping += 1
                continue
            base_id, enhancement_id = normalize_control_id(key)
            if not _CTL_KEY_RE.match((key or "").strip()):
                summary.unparsed.append(key)

            target_id = enhancement_id or base_id
            source = index.get(target_id)
            if source is None:
                summary.unresolved.append(target_id)

            parameters = _render_parameters(entry)
            guidance = _render_guidance(entry)

            summary.tailored += 1
            summary.parameters += len(parameters)
            summary.with_parameters += 1 if parameters else 0
            summary.with_guidance += 1 if guidance else 0
            if entry.get("varies_by_class"):
                summary.varies_by_class.append(target_id)

            parent = assembled.get(base_id)
            if parent is None:
                base_source = index.get(base_id)
                parent = Control(
                    control_id=base_id,
                    title=getattr(base_source, "title", ""),
                    framework="FedRAMP",
                    framework_version=version,
                    family=getattr(base_source, "family", None),
                    family_abbr=getattr(base_source, "family_abbr", None) or base_id.split("-")[0],
                    # Left unset deliberately — see the module docstring.
                    baseline=None,
                    control_statement=getattr(base_source, "control_statement", ""),
                    discussion=getattr(base_source, "discussion", ""),
                    related_controls=list(getattr(base_source, "related_controls", []) or []),
                    source_crosswalk=({NIST_SOURCE: base_id} if base_source is not None else {}),
                )
                assembled[base_id] = parent

            if not enhancement_id:
                parent.parameter_values = parameters
                parent.additional_requirements = guidance
                continue

            parent.enhancements.append(
                ControlEnhancement(
                    enhancement_id=enhancement_id,
                    title=getattr(source, "title", ""),
                    # Same reason as the parent's: there is no baseline to
                    # record, and "" says that where a guess would not.
                    baseline="",
                    description=(
                        getattr(source, "description", "")
                        or getattr(source, "control_statement", "")
                    ),
                    parameter_values=parameters,
                    additional_requirements=guidance,
                    source_crosswalk=({NIST_SOURCE: enhancement_id} if source is not None else {}),
                )
            )

    def sort_key(control: Control) -> tuple[str, int]:
        family, _, number = control.control_id.partition("-")
        return family, int(number) if number.isdigit() else 0

    controls = sorted(assembled.values(), key=sort_key)
    for control in controls:
        control.enhancements.sort(key=lambda e: _enhancement_number(e.enhancement_id))

    _require_every_rule(summary, declared=declared, not_a_mapping=not_a_mapping)
    return controls, summary


def _require_every_rule(summary: Summary, *, declared: int, not_a_mapping: int) -> None:
    """Refuse a parse that dropped a rule the dataset declares.

    **Every `CTL` entry is tailored or is not a mapping. There is no third
    outcome**, and the one this makes impossible is *silently absent*.

    `summary.unparsed` and `summary.unresolved` are **annotations, not
    exclusions** — an entry whose key does not match `_CTL_KEY_RE`, or
    whose control is missing from 800-53, is still emitted and still
    counted as tailored. So they do not appear here. Reading them as
    exclusions gives an invariant that is wrong in a way no current data
    reveals, because both lists are empty against the published dataset.

    Counted against the dataset: `declared` is summed in its own pass
    before the loop, so a loop that stopped early moves one side and not
    the other. An earlier version of this check in `arc_ampe` incremented
    both counters inside the parse loop and could not fail — truncating
    that scan dropped a catalog from 215 controls to 103 in silence.

    FedRAMP publishes no machine-readable baseline any more, so this
    dataset is the whole of what the catalog can know. A short parse
    produces a tailoring that is internally consistent and missing
    controls FedRAMP requires.
    """
    if summary.tailored + not_a_mapping != declared:
        raise ValueError(
            f"the rules dataset declares {declared} CTL entr(ies); "
            f"{summary.tailored} were tailored and {not_a_mapping} were not "
            f"mappings, leaving {declared - summary.tailored - not_a_mapping} "
            f"unaccounted for. An entry was neither read nor rejected, which "
            f"usually means the dataset changed shape."
        )


def _enhancement_number(enhancement_id: str) -> int:
    match = re.search(r"\((\d+)\)", enhancement_id or "")
    return int(match.group(1)) if match else 0


def fetch_fedramp_rules(*, url: str = RULES_URL) -> dict:
    """The FedRAMP consolidated rules dataset, as published.

    One request, no retries: an ETL an operator ran by hand should fail
    loudly rather than paper over an upstream that is down, which is a thing
    they want to know about a catalog they are about to commit.
    """
    import requests

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.json()


__all__ = [
    "CLASS_ORDER",
    "FEDRAMP_RULES_REF",
    "RULES_URL",
    "Summary",
    "fetch_fedramp_rules",
    "index_catalog",
    "normalize_control_id",
    "parse_fedramp_rules",
]

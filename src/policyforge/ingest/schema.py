"""Common shape every framework loader normalizes into.

Keeping ingest/*.py loaders separate from this schema is what lets bundled
(public-domain) sources and BYOC (licensed) sources flow through the same
mapping/synthesis/generate pipeline without the pipeline code caring where
a Control came from.

Two sub-requirement shapes live here, and the difference is not cosmetic:

* **`ControlEnhancement`** is *additive*. NIST 800-53's AC-2(1) is AC-2
  plus something more; an assessor reads both. Selecting an enhancement is
  a decision about rigour, made once per baseline.
* **`Requirement`** is *alternative*. HITRUST CSF states a control once as
  a short specification and then re-states it as a full requirement per
  **level** — Level 1, Level 2, Level 3, but also Level HIPAA, Level
  FedRAMP, Level CMS and many other regulatory and segment overlays.
  Which levels apply to you is not a rigour dial; it is computed from your
  organization's scoping factors (bed count, records held, whether you
  touch federal tax information). Two organizations assessed against the
  same control reference can be answering entirely different text.

Modelling HITRUST levels as enhancements was the tempting shortcut and it
is wrong in a way that corrupts reports: it would make "Level HIPAA" look
like extra credit on top of Level 1 rather than a parallel statement
selected by a regulatory factor, and coverage counts would double-count
every control that carries an overlay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: `Requirement.level_kind` values. A maturity level is HITRUST's 1/2/3
#: ladder, ordered and cumulative in rigour. An overlay is everything else
#: — a level named for the authority that compels it, unordered, and
#: applicable only when a scoping factor turns it on.
MATURITY = "maturity"
OVERLAY = "overlay"


@dataclass
class ControlEnhancement:
    enhancement_id: str
    title: str
    baseline: str
    description: str
    #: Same meaning as `Control.parameter_values`, at the sub-requirement
    #: level. An enhancement carries its own organization-defined values and
    #: a profile answers them separately from the parent's, so folding these
    #: upward would attribute AC-2(3)'s ninety days to AC-2.
    parameter_values: dict[str, str] = field(default_factory=dict)
    #: Same meaning as `Control.additional_requirements`.
    additional_requirements: str = ""
    # Same role as `Control.source_crosswalk` below, but at the
    # sub-requirement level. Some frameworks publish their crosswalk at both
    # levels — NIST's HIPAA-to-800-53 crosswalk, for instance, maps each
    # Required/Addressable implementation specification to its own set of
    # 800-53 controls, distinct from its parent Standard's — so folding
    # those into the parent would lose real mapping detail.
    source_crosswalk: dict[str, str] = field(default_factory=dict)


@dataclass
class Requirement:
    """One assessable requirement statement, scoped to one level.

    This is the unit an assessor actually tests. A HITRUST control
    reference is a heading; the requirement statement under a level is the
    sentence you are graded against.
    """

    requirement_id: str
    #: Verbatim level label as the export wrote it — "Level 1", "Level FTI
    #: Custodians". Kept unparsed because the vocabulary grows every CSF
    #: release, and a loader that only understood the levels known when it
    #: was written would silently drop next year's.
    level: str
    statement: str
    level_kind: str = OVERLAY
    #: The scoping answers that make this level apply. Free text as the
    #: framework words it ("Bed: Greater than 750 Beds"), one entry per
    #: line of the export's factor cell, because these are questionnaire
    #: answers rather than an enumerable type.
    organizational_factors: list[str] = field(default_factory=list)
    system_factors: list[str] = field(default_factory=list)
    regulatory_factors: list[str] = field(default_factory=list)
    #: `{authoritative source: [identifiers]}` — HITRUST's own crosswalk,
    #: published per requirement statement rather than per control. A list
    #: rather than the single string `source_crosswalk` carries because one
    #: requirement routinely maps to a dozen 800-53 controls, and joining
    #: them into one string would only force every reader to split it again.
    mappings: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Control:
    control_id: str
    title: str
    framework: str  # e.g. "NIST-800-53", "FedRAMP", "ARC-AMPE", "HITRUST-CSF", "GovRAMP"
    framework_version: str  # e.g. "Rev 5", "v11.8"
    family: str | None = None
    family_abbr: str | None = None
    baseline: str | None = None
    control_statement: str = ""
    discussion: str = ""
    enhancements: list[ControlEnhancement] = field(default_factory=list)
    related_controls: list[str] = field(default_factory=list)
    # Raw framework-ID crosswalk as captured at the source (e.g. the HITRUST
    # column in a NIST control note). Kept separate from mapping/crosswalk.py's
    # curated crosswalk so provenance is always traceable back to the loader
    # that produced it.
    source_crosswalk: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None

    # --- Frameworks that profile another framework -------------------------
    # A profile (GovRAMP, FedRAMP) does not write its own catalog. It selects
    # controls from 800-53, reproduces their text, and adds two things the
    # base catalog deliberately leaves open. Both are the reason to ingest a
    # profile at all, so neither is folded into `control_statement`, where it
    # would become indistinguishable from the text NIST wrote.
    #
    #: The organization-defined values the profile has already decided,
    #: keyed by the citation it writes them against: `{"AC-1 (c) (1)": "at
    #: least every 3 years"}`. A dict rather than prose because
    #: `parameters/ledger.py` asks exactly this question — what is this
    #: control's frequency — and an assessor-defensible answer with a
    #: citation already exists here. The key is the profile's own wording,
    #: not a slug: it points at a sub-paragraph (`(c) (1)`) that only the
    #: source document numbers.
    parameter_values: dict[str, str] = field(default_factory=dict)
    #: Requirements and guidance the profile adds on top of the base control
    #: — "the service provider defines the time period for non-user
    #: accounts". Verbatim, line breaks kept, because these are normative
    #: sentences an assessor reads and not a summary.
    additional_requirements: str = ""

    # --- Frameworks with a tier between family and control -----------------
    # HITRUST nests Category > Objective > Control Reference, and the middle
    # tier is not decoration: an objective states the *purpose* several
    # control references share, which is exactly the sentence a synthesized
    # policy's opening paragraph wants. `family` carries the category, so
    # these three carry the objective rather than overloading it.
    objective_id: str = ""
    objective_title: str = ""
    objective_statement: str = ""
    #: HITRUST's "Factor Type": whether this control's applicability is
    #: driven by organizational factors or system factors. Not the same
    #: axis as `Requirement.level_kind`.
    factor_type: str = ""
    requirements: list[Requirement] = field(default_factory=list)


#: The marker a licensed ETL writes into the catalog it produces (#459 (b),
#: 80's ruling): the file then carries its own licence, so its
#: classification no longer depends on where it is saved.
LICENSED_MARKER = "licensed"

#: The envelope values that say a catalog is public. Anything else an
#: envelope declares, or an envelope that declares nothing, is licensed
#: (#320, 80's ruling): fail closed, as `frameworks.registry` already reads a
#: manifest. The registry's own public value; a test holds the two equal.
PUBLIC_LICENCES = frozenset({"public-domain"})


def envelope_licence(data: dict) -> str:
    """An envelope's licence as written, under `licence` or `license`, or ""
    when it names neither (#320: the American spelling of the key read as
    no licence at all).

    Both keys are read. If either value is licensed, that value is returned,
    so the envelope is public only when every licence it states is public:
    `{"licence": "public-domain", "license": "licensed"}` letting the first
    key win read public (ba on PR 333)."""
    values = [str(data[key]) for key in ("licence", "license") if data.get(key) is not None]
    if not values:
        return ""
    return next((value for value in values if is_licensed(value)), values[0])


def is_licensed(licence: str | None) -> bool:
    """Whether a catalog's declared licence (`parse_catalog`'s second value)
    makes it licensed: THE one rule, which every reader of an envelope calls,
    `scripts/publish_guard.py`'s L4 included (#320).

    None is a plain list, which declares nothing, so its path decides, as
    before. Any envelope value but an explicit public one is licensed,
    compared trimmed and casefolded, so `Licensed`, `LICENSED`, `licenced`,
    `" licensed "` and an envelope with no value all count. Writing is
    unchanged: `catalog_json` writes `LICENSED_MARKER` exactly.
    """
    if licence is None:
        return False
    return licence.strip().casefold() not in PUBLIC_LICENCES


def parse_catalog(text: str, *, source: object = "the catalog") -> tuple[list, str | None]:
    """`read_catalog` for text already read, e.g. from `git show` (`drift`
    reads the COMMITTED version of a catalog this way). The one rule for both
    forms; `read_catalog` is this plus reading the file (1d on #459 (b): a
    text-level reader that bypassed it raised on an envelope)."""
    data = json.loads(text)
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict) and isinstance(data.get("controls"), list):
        # "" for an envelope that names no licence, never None: None means a
        # plain list, and `is_licensed` reads an envelope's silence as licensed.
        return data["controls"], envelope_licence(data)
    raise ValueError(
        f"{source} is not a catalog: expected a list of controls, or an object with a "
        "'controls' list."
    )


def read_catalog(path: Path) -> tuple[list, str | None]:
    """(the catalog's rows, the licence it declares or None): the ONE reader.

    A catalog is either a JSON list of controls (every shipped catalog, and
    every catalog written before #459 (b)) or an **envelope** a licensed ETL
    writes, `{"licence": "licensed", "controls": [...]}`. Every reader goes
    through this, so no reader meets an envelope unprepared: one that did
    would see a dict where it expected a list, and several of them skip what
    they cannot read, which would turn a licensed catalog into a silent gap.
    Anything else is refused rather than read as empty. Text already in hand
    (not a path) goes through `parse_catalog`, the same rule.
    """
    return parse_catalog(Path(path).read_text(encoding="utf-8"), source=path)


def catalog_json(rows: list, *, licence: str | None = None) -> str:
    """A catalog's file content: a plain list, or the licensed envelope.

    `licence` first in the envelope, so a person opening the file sees it
    before a thousand rows.
    """
    if licence is None:
        return json.dumps(rows, indent=2)
    return json.dumps({"licence": licence, "controls": rows}, indent=2)


def load_controls(path: Path) -> list[Control]:
    """Load a controls.json produced by any loader's `dataclasses.asdict`
    dump (see the `etl-vault` command in cli/etl.py) back into `Control` objects.

    Tolerant of catalogs written before `requirements` existed: a missing
    key is an empty list, not a crash. Files on disk outlive the schema
    that wrote them, and re-running an ETL to read an old catalog is a poor
    trade for a `pop` with a default.
    """
    data, _ = read_catalog(path)
    controls = []
    for item in data:
        enhancements = [ControlEnhancement(**e) for e in item.pop("enhancements", [])]
        requirements = [Requirement(**r) for r in item.pop("requirements", [])]
        controls.append(Control(enhancements=enhancements, requirements=requirements, **item))
    return controls

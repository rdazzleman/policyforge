"""NIST CSF 2.0: the Core from NIST's OSCAL, and NIST's mapping to 800-53 (#408).

**Two sources, both pinned by their bytes.**

- The Core: `oscal_loader.CATALOG_URL_CSF_2_0`, the `usnistgov/oscal-content`
  file at tag `v1.5.0`, the same tag the 800-53 catalog is pinned to.
- The mapping: OLIR 186, "Cybersecurity-Framework-v2.0-to-SP-800-53-Rev-5-2-0",
  a workbook NIST publishes separately (the OSCAL catalog carries no 800-53
  links). **NIST marks it `comprehensive: No`**, its file name says `draft`,
  and its SHA-256 does not match the hash OLIR publishes for it (f8 on #408;
  both values are in the catalog README). So it is pinned to OUR hash of the
  file we fetched, as the AI RMF Playbook is, and any other file is refused.

**What the mapping becomes** (80's rulings on #408):

- A control-level link goes into the subcategory's `source_crosswalk` under
  `nist-800-53`, zero padding removed (`IR-04` -> `IR-4`, `CM-7(02)` ->
  `CM-7(2)`). Every target must be an id of the shipped 800-53 catalog.
- A **family-level** target (`GV.OC-03 -> PT`) is NIST asserting a link to a
  whole family, not to a control. It is kept as a typed family link and
  never expanded to the family's controls: expanding asserts control-level
  mappings NIST withheld. It is written under `family_links:` in the
  catalog's `framework.yaml`, not into any `Control`, so no control-level
  count can include it (80's ruling: by construction, not by filter). A field
  on `Control` would have done the same and changed every shipped catalog's
  serialised form, since each is `dataclasses.asdict` of the schema; a
  fourth file would need `init` to learn to ship it.
- A target that resolves to nothing is refused BY NAME. The one NIST's file
  carries today, `DE.AE-06 -> RA-4` (RA-4 is withdrawn in rev 5), is listed
  in `KNOWN_UNRESOLVED`; any other stops the run, so a new one is read by a
  person rather than dropped.
- The 26 rows naming a function or category with no target are the
  workbook's section headers, not links, and carry nothing.
"""

from __future__ import annotations

import hashlib
import io
import re

#: OLIR 186, as NIST's OLIR record names its file (f8 on #408).
OLIR_186_URL = (
    "https://csrc.nist.gov/csrc/media/projects/olir/documents/submissions/"
    "Cybersecurity_Framework_v2-0_Concept_Crosswalk_800-53_5_2_0_draft.xlsx"
)
#: Our SHA-256 of that file, measured by f8 and ba on 2026-09-26. NOT the
#: hash OLIR publishes (FD71716B...), which matches no file NIST serves.
OLIR_186_SHA256 = "5521fa73ace64d8a3014a7b1e971f0f20d32df5ff5e174632723bda09e7e908f"
#: Our SHA-256 of the pinned OSCAL catalog (the file, not the parsed output).
CATALOG_SHA256 = "4836943f21d393f2821df85ada4e6f3c0617243b4ddf44263fe68205400210b4"

#: The one target in OLIR 186 that names no control of rev 5: RA-4 was
#: withdrawn. Refused and reported, never carried. A second one stops the run.
KNOWN_UNRESOLVED = frozenset({("DE.AE-06", "RA-4")})

_CONTROL = re.compile(r"^([A-Z]{2})-0*(\d+)(?:\(0*(\d+)\))?$")
_FAMILY = re.compile(r"^[A-Z]{2}$")


class CsfError(ValueError):
    """A CSF source that is not the pinned one, or a mapping that does not
    resolve as the pinned one does. Nothing is written."""


def require_sha256(raw: bytes, expected: str, what: str) -> None:
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise CsfError(
            f"{what} has SHA-256 {actual}, not the pinned {expected}. NIST has changed "
            "the file, or this is not it; read the change before re-pinning."
        )


def normalise_control(target: str) -> str | None:
    """`IR-04` -> `IR-4`, `CM-7(02)` -> `CM-7(2)`; None when not control-shaped."""
    match = _CONTROL.match(target.strip())
    if not match:
        return None
    family, number, enhancement = match.groups()
    return f"{family}-{number}" + (f"({enhancement})" if enhancement else "")


def parse_olir_186(raw: bytes, catalog_ids: set[str]):
    """({focal id: [800-53 ids]}, {focal id: [families]}, [(focal, target) refused]).

    `catalog_ids` is every control and enhancement id of the shipped 800-53
    catalog. Rows are read from the `Relationships` sheet in file order.
    """
    import openpyxl

    sheet = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)["Relationships"]
    controls: dict[str, list[str]] = {}
    families: dict[str, list[str]] = {}
    refused: list[tuple[str, str]] = []
    for row in list(sheet.iter_rows(values_only=True))[1:]:
        focal = str(row[0] or "").strip()
        target = str(row[2] or "").strip() if len(row) > 2 else ""
        if not focal or not target:
            continue  # a section header: a function or category, no link
        if _FAMILY.match(target):
            families.setdefault(focal, []).append(target)
            continue
        control = normalise_control(target)
        if control is None or control not in catalog_ids:
            refused.append((focal, control or target))
            continue
        if control not in controls.setdefault(focal, []):
            controls[focal].append(control)
    unexpected = [pair for pair in refused if pair not in KNOWN_UNRESOLVED]
    if unexpected:
        raise CsfError(
            f"OLIR 186 names target(s) no shipped 800-53 id resolves: {unexpected}. "
            "Refusing rather than dropping them."
        )
    return controls, families, refused


def attach(controls, links: dict[str, list[str]], families: dict[str, list[str]]) -> dict:
    """Put OLIR 186's control links on the parsed Core, in place, and return
    the family links as `framework.yaml`'s `family_links:` value.

    A focal id is a category (a `Control`) or a subcategory (one of its
    enhancements); OLIR 186 links both. A focal id the Core does not have is
    refused by name: it would be a link with nowhere to go, and dropping it
    understates the source.
    """
    from policyforge.ingest.oscal_loader import CROSSWALK_KEY_800_53

    nodes = {}
    for control in controls:
        nodes[control.control_id] = control
        for enhancement in control.enhancements:
            nodes[enhancement.enhancement_id] = enhancement
    missing = sorted((set(links) | set(families)) - set(nodes))
    if missing:
        raise CsfError(
            f"OLIR 186 links CSF id(s) the pinned Core does not have: {missing}. Refusing."
        )
    for focal, ids in links.items():
        nodes[focal].source_crosswalk[CROSSWALK_KEY_800_53] = ", ".join(ids)
    return {
        "relationship": "family",
        "framework": CROSSWALK_KEY_800_53,
        "note": "NIST links each of these CSF ids to a whole 800-53 family, not to any "
        "control in it. Never expanded to the family's controls.",
        "links": {focal: list(abbrs) for focal, abbrs in families.items()},
    }


def record_family_links(framework_yaml, family_links: dict) -> None:
    """Write `family_links:` into the catalog's manifest; nothing when there is
    none, as `record_source_provenance` does for a scratch `--out`."""
    import yaml

    from policyforge.textfile import write_text_lf

    if not framework_yaml.exists():
        return
    data = yaml.safe_load(framework_yaml.read_text(encoding="utf-8")) or {}
    data["family_links"] = family_links
    write_text_lf(
        framework_yaml, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
    )


def fetch(url: str) -> bytes:
    import requests

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content

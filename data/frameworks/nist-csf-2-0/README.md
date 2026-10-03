# NIST Cybersecurity Framework 2.0

The CSF 2.0 Core, as a control catalog: **22 categories carrying 106
subcategories**, across the six functions NIST defines. The 91 CSF 1.1
elements that NIST's file still carries as `withdrawn` are excluded.

| Function  | Categories | Subcategories |
| --------- | ---------- | ------------- |
| Govern    | 6          | 31            |
| Identify  | 3          | 21            |
| Protect   | 5          | 22            |
| Detect    | 2          | 11            |
| Respond   | 4          | 13            |
| Recover   | 2          | 8             |
| **Total** | **22**     | **106**       |

Every number above is derived from `controls.json` by
`tests/test_csf_catalog.py`, which fails if this table and the catalog
disagree.

A category (`GV.OC`) is a `Control` and its subcategories (`GV.OC-01`) are
its enhancements, the same shape 800-53 and the AI RMF Core use.

## Read this before citing the catalog

**This catalog states outcomes, not obligations.** The AI RMF Core is the
same, and the difference changes what a citation to it proves.

800-53 says *the organization shall*. CSF 2.0 says things like:

> The organizational mission is understood and informs cybersecurity risk
> management.

That is a state of the world, not an instruction. Nobody is named, no act
is required, and there is no test an assessor could fail you against.

`satisfies` will resolve `[NIST CSF 2.0 GV.OC-01]`, the tag is legal, and
the gate goes green. None of that establishes that the sentence carrying
the tag commits anyone to anything. It may simply restate the outcome in
new words. `check` treats a CSF citation exactly as it treats an AI RMF Core
citation. Cited beside the AI RMF Playbook, it does not make a sentence
binding, so the Playbook's rules still govern it. And, as for the Core,
**nothing in `check` tells a hollow citation from a substantive one**. That
is a known gap, stated rather than papered over. When
reviewing generated content that cites this catalog, ask *what would
someone have to do differently tomorrow?* If the answer is "nothing", the
citation is decorative even though every automated check passed.

A topic does not anchor this catalog. It is reached through NIST's own
mapping to 800-53, below: a topic anchors 800-53 controls, and the CSF
outcomes those controls map to are reported beside them.

## NIST's mapping to 800-53, and its four caveats

The Core carries no 800-53 links. NIST publishes them separately, as
**OLIR 186** ("Cybersecurity-Framework-v2.0-to-SP-800-53-Rev-5-2-0"), and
this catalog carries that mapping: **742 links to 800-53 controls and
enhancements on 108 CSF ids** (every subcategory, plus the categories
`RS.MA` and `RC.RP`, which NIST links directly), every one of them resolved
against this project's 800-53 catalog.

**1. NIST states no relationship for any link.** It does not say whether
a control covers a subcategory in full, in part or only intersects it. So
this catalog's `framework.yaml` declares `crosswalk_relationship: source-untyped`, and `/coverage` and `satisfies` read every CSF link as
reaching the subcategory **in part**: a person's call, never full coverage.
`source-untyped` is this project's value, not a NIST relationship type, so
a row carrying it cannot be read as NIST's claim. An organisation that
reviews a link and records a relationship in its crosswalk overlay
overrides it. `crosswalk seed` writes `source-untyped` for these pairs, so
seeding changes nothing until someone edits the file.

**2. NIST marks the mapping not comprehensive.** OLIR's record for it
says `comprehensive: No`. A CSF id with no link is **not mapped by NIST**,
which is not the same as having no 800-53 equivalent. `/coverage` says so
beside the CSF figures, naming the source from `crosswalk_source:` in
`framework.yaml`. With the pinned file, every subcategory has at
least one link and 20 of the 22 categories have none.

**3. Three links name a whole 800-53 family, not a control:** `GV.OC-03`
to `PT`, and `PR.IR-03` to `CP` and to `IR`. They are kept as NIST wrote
them, under `family_links:` in `framework.yaml` with `relationship: family`. They are never expanded to the family's controls, which would
assert control-level mappings NIST did not make, and they are never counted
as control-level coverage. They sit outside `controls.json`, so no
control-level count can include them.

**4. One target names no control of 800-53 rev 5:** `DE.AE-06` to `RA-4`,
which rev 5 withdrew. `etl-csf` refuses it by name and reports it on every
run. Any other target that resolves to nothing stops the run rather than
being dropped.

### The file NIST serves is not the file OLIR describes

Recorded verbatim, because the pin depends on it:

- OLIR's record names the file
  `Cybersecurity_Framework_v2-0_Concept_Crosswalk_800-53_5_2_0_draft.xlsx`.
  The file name says **draft**.
- OLIR publishes its hash as
  `FD71716B33ADB4323E0E493E19501F6413489FB533F9B741E8A462145385FCF2`.
- The file served at that address hashes to
  `5521fa73ace64d8a3014a7b1e971f0f20d32df5ff5e174632723bda09e7e908f`.

Nobody here can tell whether OLIR's hash covers a different revision, a
different encoding or a different algorithm. So this catalog is pinned to
**our** hash of the file we fetched, as the AI RMF Playbook is, and
`etl-csf` refuses any other file.

## Source

|          |                                                                                                                 |
| -------- | --------------------------------------------------------------------------------------------------------------- |
| Core     | NIST `usnistgov/oscal-content`, tag `v1.5.0` (the same tag as the 800-53 catalog), `NIST_CSF_v2.0_catalog.json` |
| Mapping  | NIST OLIR 186, the workbook above                                                                               |
| Licence  | Public domain (a US government work)                                                                            |
| Revision | **CSF 2.0**, CSWP 29, February 26, 2024: <https://doi.org/10.6028/NIST.CSWP.29>                                 |
| Command  | `policyforge etl-csf`                                                                                           |

**`source_ref` is `1.2.0`, and that is not the tag.** It is the catalog's
own `metadata.version`, which NIST versions separately from the
`oscal-content` repository tag (`v1.5.0`) that `source_url` names. Both are
recorded; neither stands for the other.

**The OSCAL file cites the draft.** Its back-matter points at
`NIST.CSWP.29.ipd`, the initial public draft. Its content matches the
final publication, which is what this catalog cites.

Both files are pinned by SHA-256, in `framework.yaml` (`source_sha256`,
`crosswalk_source_sha256`) and in `policyforge/ingest/csf.py`. On the
scheduled drift job, a refusal means NIST has published a different file,
and the red is the job working. Read the change before re-pinning.

## Parser notes

`etl-csf` refuses, rather than writes, a parse that is not exactly 6
functions, 22 categories and 106 subcategories. It refuses more as firmly
as fewer: a longer parse usually means withdrawn CSF 1.1 elements got
through, and a catalog carrying `ID.AM-06` would publish an id CSF 2.0 no
longer has. It also refuses ids that are not CSF-shaped, because a dialect
reading the wrong field produces the right count of wrong ids.

NIST pads its 800-53 ids (`IR-04`, `CM-07(02)`). The padding is removed
before a target is resolved, and a target is carried only if it is a
control or enhancement id of this project's 800-53 catalog.

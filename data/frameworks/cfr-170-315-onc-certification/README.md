# ONC Certification Criteria (45 CFR 170.315)

Public domain (US federal regulation — 45 CFR 170.315, the certification
criteria for health IT). Same basis as NIST 800-53/FedRAMP/ARC-AMPE/HIPAA, so
unlike HITRUST/GovRAMP this is safe to bundle directly rather than treat as
BYOC.

**This catalog shipped once with fabricated criteria and was withdrawn** (#152,
reverted by #163). Categories (b) and (e) held entries that do not exist, and
nothing in the catalog looked wrong. This version is refused by its own loader
unless its criteria **equal** a set that two independent sources agree on. See
"How it is kept honest" below.

## What an entry is

**One criterion is one control**, cited the way developers and ONC's own
programme documents cite them:

```
170.315(g)(10)   Standardized API for patient and population services
170.315(d)(1)    Authentication, access control, authorization
170.315(b)(1)    Transitions of care
```

The lettered category above a criterion is its family, for example `(g) Design and performance`. The sub-paragraphs below it are the control statement: they
describe the conditions of one capability, not separate duties.

**This is a certification catalog, and that is not the same as a control
catalog.** A criterion says what a Health IT Module must be *able to do* to be
certified. Citing `170.315(g)(10)` asserts that a capability exists in a
product. It does not assert that your organization has implemented, configured
or operates it.

## What is in it

59 live criteria across 9 categories, from eCFR as current on 2026-09-22.

| Category |                                       | Criteria |
| -------- | ------------------------------------- | -------- |
| `(a)`    | Clinical                              | 8        |
| `(b)`    | Care coordination                     | 9        |
| `(c)`    | Clinical quality measures             | 4        |
| `(d)`    | Privacy and security                  | 13       |
| `(e)`    | Patient engagement                    | 2        |
| `(f)`    | Public health                         | 7        |
| `(g)`    | Design and performance                | 12       |
| `(h)`    | Transport methods and other protocols | 2        |
| `(j)`    | Modular API capabilities              | 2        |

**Left out, and reported by id on every run:**

- **Reserved criteria.** A criterion is reserved only when its *own* text is
  `[Reserved]`. `170.315(b)(3)` opens with a reserved first sub-paragraph and
  is live. The previous version dropped it, because it tested for the marker
  anywhere in the text instead of for what the marker governs.
- **Expired criteria.** A criterion whose own text says its adoption has
  expired, as of the eCFR date. `170.315(a)(9)` expired on 1 January 2025.
- **Reserved categories.** `170.315(i)` exists only as a reserved placeholder.

**Pending, and not applied:** the ASTP/ONC **proposed** rule *Deregulatory
Actions To Unleash Prosperity* (90 FR 60970, 2025-12-29)
would remove **many** § 170.315 criteria, across several categories. The FY2027 IPPS final rule
(91 FR 49570, 2026-08-04) describes three of them, `170.315(a)(14)`, `(h)(1)`
and `(h)(2)`, as proposed for removal from the Base EHR definition. That is a
subset, not the whole proposal. **No count of the proposed removals is given
here, because none has been independently measured.** No final rule amending
§ 170.315 has been published since HTI-4 (last amended 2025-10-01, per eCFR)
as of 2026-09-24, so every criterion in this catalog is still live. **If that rule
is finalised, the framework-drift job goes red, and that is the job working.**
A person then re-derives the agreed set (#179) and re-pins the catalog; the
loader refuses a changed set rather than absorbing it.

## How it is kept honest

§ 170.315 is one section of about 540 flat paragraphs, and its hierarchy exists
only in the text. `(1)` can be a criterion, or an item three levels inside the
criterion before it. The first version read the text alone, and a nested list
item became `(b)(2)`, then `(b)(3)`, and so on.

**eCFR's markup carries the distinction.** The two deepest levels have
*italic* markers, `(1)` and `(i)` set in italics, and the four levels above
them do not. The loader reads the markup rather than the text, and places every
marker at a level where it is the next value, the first child of the level
above, or a repeat of the current value. (eCFR carries two dated versions of
`(b)(2)(iv)`.) **A marker that fits no level is an error, never a guess.**

**Then the result is refused unless it equals an independent set.**
This project's research found the live criteria from two sources on #179. Neither
reads this section's paragraph text:

- ONC's test-method index;
- the Federal Register's amendment instructions (the Cures Act rule, HTI-1,
  HTI-2 and HTI-4), applied to CHPL's list of every criterion ever used.

They agree exactly, on the same 59, including `(b)(11)`, which has no italic
heading and defeated one of the earlier rules. A parse that invents or loses a
criterion is refused with both lists, and nothing is written. That includes the
regulation changing, which a person then reviews.

## Why it is not called "45 CFR 170"

A framework name beginning with a digit is **not a legal source tag**, because
`content/tags.SOURCE_TAG_RE` builds a name out of capital-initial words. A
catalog declaring `45 CFR 170` could not be cited by any document. The declared
name comes from the section's own heading, "ONC certification criteria for
Health IT", and is pinned to this catalog's key in `mapping/crosswalk`. Other
ONC programme names are not pinned, because nobody has read them in a catalog
yet (#176).

## Regenerating it

```
policyforge etl-onc
```

`framework.yaml` records the eCFR date fetched, the exact URL, and the SHA-256
of the catalog produced. eCFR has no release tags, so the effective date is the
only thing that names a revision. The monthly `framework-drift` job re-runs this
and fails when the regulation has moved, including when the loader refuses a
changed criterion set. That failure is the job working.

## Crosswalk

This catalog ships without a `source_crosswalk`. No authority publishes a
mapping from certification criteria to 800-53, because a criterion is a
statement about product capability and a control is a statement about
organizational practice. Your organization can record its own reviewed mapping
with `policyforge crosswalk seed`. That mapping is your decision, not the
regulation's.

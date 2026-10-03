# HIPAA Security Rule

Public domain (US federal regulation — 45 CFR Part 164, Subpart C:
"Security Standards for the Protection of Electronic Protected Health
Information"). Same basis as NIST 800-53/FedRAMP/ARC-AMPE, so unlike
HITRUST/GovRAMP this is safe to bundle directly rather than treat as BYOC.

Populated: 34 top-level Standards carrying 40 Required/Addressable
implementation specifications between them (74 requirements in total),
drawn from §§ 164.306, 164.308, 164.310, 164.312, 164.314, 164.316,
164.318 — the definitions section, § 164.304, is intentionally excluded
since defined terms aren't requirements.

**What is left out of each statement, and where it lives instead.** Four
sections open with the same framing sentence before their first standard:
§§ 164.308(a), 164.310, 164.312 and 164.316 each begin *"A covered entity
or business associate must, in accordance with § 164.306:"*. The loader
drops that sentence, deliberately, so each control's statement reads as the
regulation's own requirement (*"Implement …"*) rather than repeating it on each of the
20 standards those sections hold. So:

- **who the obligation runs to**, a covered entity or business associate, is
  **not** visible on those controls;
- **that it applies "in accordance with § 164.306"** is carried in each of
  those 20 controls' `related_controls`, as `164.306`, the section exactly
  as the lead-in cites it: the general rules, including flexibility of
  approach (§ 164.306(b)) and the Required and Addressable distinction
  (§ 164.306(d)).

**§ 164.306 itself ships as five controls**, `164.306(a)` to `(e)`, and each
specification keeps its own Required or Addressable tag, so the rules are in
the catalog and `164.306` names them. The loader reads the relationship from
the lead-in's own text, and refuses a parse in which a lead-in citing a
section frames no control. § 164.302 (Applicability) and
§ 164.304 (Definitions) are not shipped as controls, because neither states a
requirement to implement.

Source: eCFR's public versioner API,
<https://www.ecfr.gov/api/versioner/v1/full/%7Bdate%7D/title-45.xml?part=164>
— not a hand-copied transcription, so this can always be regenerated and
diffed against the actual current regulation text:

```
policyforge etl-hipaa
```

Only the Security Rule (Subpart C) is parsed — HIPAA's Privacy Rule
(Subpart E) and Breach Notification Rule (Subpart D) are out of scope for
this security-focused pipeline. See `ingest/hipaa_loader.py` for parsing
details, including how it resolves citation-hierarchy ambiguity and why
Required/Addressable tagging can't be assumed to sit at a fixed nesting
depth.

## NIST 800-53 crosswalk

**Wired into `mapping/crosswalk.py`.** Each Standard and implementation
specification carries NIST's official mapping to SP 800-53 Rev 5 in its
`source_crosswalk["nist"]`, so `build_crosswalk()` resolves a NIST control
ID to its HIPAA equivalents and `synthesize` pulls HIPAA requirements into
a NIST-anchored topic.

Coverage: **65 of the 74** requirements are mapped (25 Standards, 40
implementation specifications) across **278 distinct citation-to-control
pairs**, reaching **108 distinct SP 800-53 controls**. The 9 unmapped are
ones NIST's crosswalk doesn't cover, and are reported by name each time the
ETL runs rather than left invisible: § 164.306(a)-(e) (general rules),
§ 164.318(a)-(c) (compliance dates), and the bare "Implementation
specifications" container paragraph § 164.308(a)(5)(ii), whose child
specifications *are* individually mapped.

§ 164.314(a)(2) was a second such container and is **no longer emitted**.
It carried a title and no text, so citing it resolved to nothing while the
obligation sat in its children — and unlike § 164.308(a)(5)(ii), whose
children are nested inside it, its three children ship as top-level
controls in their own right: § 164.314(a)(2)(i), (ii) and (iii). Cite
those. Removing it is why the totals above are 74 and 9 rather than 75 and
10; no requirement was lost.

### Source

NIST's **Cybersecurity and Privacy Reference Tool (CPRT)** catalog
`HIPAA-Security-Rule-to-SP-800-53-Rev-5.1.1`:

|                              |                                                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------------------------ |
| Framework version identifier | `HIPAA-Sec-Rule-800-53-5.1.1`                                                                          |
| Version                      | 1.0.0                                                                                                  |
| NIST OLIR hash               | `80EE1B51D5B48F394E35311A9A93006D2F9E530E35266C6F1F00EB212B776B95`                                     |
| Retrieved                    | 2026-08-28                                                                                             |
| Landing page                 | <https://csrc.nist.gov/projects/cprt/catalog#/cprt/framework/version/HIPAA-Sec-Rule-800-53-5.1.1/home> |

Fetched from CPRT's public API — one request per OLIR element type
(`olir_entry`, `fde`, `rde`) — not hand-transcribed, so it can be
regenerated and diffed against what NIST currently publishes:

```
policyforge etl-hipaa-crosswalk
```

```
https://csrc.nist.gov/extensions/nudp/services/json/nudp/framework/version/HIPAA-Sec-Rule-800-53-5.1.1/type/{element_type}/elements
```

**Not from the SP 800-66r2 PDF.** SP 800-66 Rev. 2 is the natural-looking
source, but its Appendix D states that "the mapping table has been removed
from the document and placed online in the NIST Cybersecurity and Privacy
Reference Tool (CPRT)" — the published PDF contains no SP 800-53 control
identifiers anywhere in its 122 pages. CPRT is the authoritative source.

### Reproducibility and auditing

The exact CPRT responses this data was built from are committed verbatim at
`tests/fixtures/cprt_hipaa_to_800-53r5.json`, so the mapping is auditable
without network access and a NIST-side revision shows up as a fixture diff:

```
policyforge etl-hipaa-crosswalk --fixture tests/fixtures/cprt_hipaa_to_800-53r5.json
```

Both forms produce byte-identical output as of the retrieval date above.

Two source-shape mismatches are reconciled by the `CITATION_ALIASES` table
in `ingest/hipaa_crosswalk_loader.py` — CPRT cites some Standards one
paragraph level higher than the CFR codifies them, and a few citations
reflect a pre-Omnibus paragraph numbering. Every alias is asserted
text-equivalent against the eCFR data in
`tests/test_hipaa_crosswalk_loader.py`, so a CFR renumbering or CPRT
revision fails the test suite instead of silently mis-mapping a
requirement. Nothing is mapped by inference: `tests` also assert that every
stored control ID traces back to an actual published CPRT row.

## Which requirements are read together

`framework.yaml` declares `family: structure`: a **standard** and its
implementation specifications are one requirement for `drift`'s document
reach, and a citation of a standard by its paragraph
(`[HIPAA Security Rule 164.308(a)(1)]`, where the catalog's id is
`164.308(a)(1)(i)`) resolves to that standard in `drift` and `/satisfies`
alike (#423). The standards are the catalog's top-level entries, as the
regulation's own structure makes them, with two exceptions the manifest
declares:

- **`164.314(a)(2)(i)`, `(ii)` and `(iii)`** are top-level entries here, but
  the regulation makes § 164.314(a)(2) the *implementation specifications*
  of the standard § 164.314(a)(1), so they belong to its family.
- **`164.306(d)`** is nested under `164.306(c)` for structure only: the two
  paragraphs define "standards" and "implementation specifications", so
  `(d)` is its own family.

A test fails if either list names an id this catalog no longer has.

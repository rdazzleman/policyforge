# HIPAA Breach Notification Rule (45 CFR 164 Subpart D)

**7 sections carrying 31 paragraphs**, §§164.400–414, with §164.402
(Definitions) excluded, as the Security Rule's catalog excludes its own.
Each section is a control, and each paragraph is an enhancement under the
citation **eCFR itself gives it**. Nothing is derived from the paragraph
markers. On this subpart, the Security Rule's parser mis-cites 6 of 28
paragraphs (measured by this project's research).

Nothing in this subpart was touched by the 2024 amendments that *Purl v.
HHS* vacated. Those are in the Privacy Rule; see its README.

## Four paragraphs eCFR gives no citation

In §164.404(d), eCFR prints `(d)(1)(i)` inside `(d)(1)`'s own text, and
prints `(d)(1)(ii)`, `(d)(2)(i)` and `(d)(2)(ii)` as unnumbered paragraphs.
eCFR even links to `164.404(d)(1)(i)`, an id it never assigns. This catalog
keeps each of them inside the paragraph eCFR puts it in, so their words are
carried under `164.404(d)(1)` and `164.404(d)(2)`. Nothing is invented for
them. A document citing `164.404(d)(1)(ii)` resolves to the section,
§164.404, the unit this catalog declares (`family: section`), as `satisfies`
reports it. It does not resolve to `(d)(1)`.

A section's unnumbered opening sentence is its statement. §164.412 opens
with the law-enforcement condition that scopes both of its paragraphs.

## No published mapping to 800-53

As for the Privacy Rule, NIST's OLIR and CPRT catalogs map only the
Security Rule, and HHS and third parties were not searched. This
catalog is **not anchored**, is not counted in `/coverage`'s 800-53 figures,
and reads there as "no published crosswalk found". A document citing it is
reached by `drift` through its own ids, with the section as the unit.

## Source

|         |                                                                                      |
| ------- | ------------------------------------------------------------------------------------ |
| Text    | eCFR renderer, Title 45 Part 164 Subpart D, point-in-time **2026-09-17**             |
| Guard   | eCFR versioner XML for the same date: each section's words must equal the renderer's |
| Licence | Public domain (a US federal regulation)                                              |
| Command | `policyforge etl-hipaa-privacy` (writes this catalog and the Privacy Rule's)         |

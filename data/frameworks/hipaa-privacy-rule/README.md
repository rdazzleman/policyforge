# HIPAA Privacy Rule (45 CFR 164 Subpart E)

## Read this first: eCFR still prints text a court vacated

In 2025 a federal court vacated most of the 2024 amendments to the Privacy
Rule, *HIPAA Privacy Rule To Support Reproductive Health Care Privacy*
(89 FR 32976, FR Doc. 2024-08503):

> "Accordingly, the HIPAA Privacy Rule to Support Reproductive Health Care
> Privacy at 89 Fed. Reg. 32976 is VACATED per 5 U.S.C. Section 706(2),
> except its modifications to 45 C.F.R. Section 164.520. But the provisions
> at 45 C.F.R. Section 164.520(b)(1)(ii)(F), (G), and (H) are VACATED per
> 5 U.S.C. Section 706(2)."

That is *Purl v. HHS*, No. 2:24-cv-228-Z (N.D. Tex.), Amended Judgment
(ECF 114), filed 2025-07-03. The appeal, No. 25-10743, was dismissed on
2025-09-10. The quotation is transcribed from the court's scanned PDF, and
its citations were repaired from OCR (this project's research, recorded on #409).

**eCFR has not removed the vacated text, and this catalog carries eCFR's
text as eCFR prints it.** It does not splice in older text, because a
catalog is one source's revision, and a spliced one would be a document
nobody published. Instead, `framework.yaml` marks the affected paragraphs
under `vacated:`:

- **45 units the rule added are vacated, and nothing binds in their
  place:** all of §164.509, all of §164.535, §164.502(a)(5)(iii) and its
  paragraphs, §164.512(c)(3), and §164.520(b)(1)(ii)(F)–(H). Generation
  never uses them, and `policyforge check` warns on a citation to one,
  naming the judgment.
- **14 units the rule revised carry vacated wording:** §164.502(a)(1)(vi)
  and (g)(5) with their paragraphs, §164.512's opening sentence, the
  §164.512(c) heading, and §164.512(f)(1)(ii)(C). What binds again is the
  **pre-rule** wording, which eCFR no longer prints. `framework.yaml`
  quotes it from eCFR's own text as of 2024-04-25, with that file's URL and
  SHA-256, as an attributed annotation. Generation uses the quoted
  wording. A citation to one of these paragraphs is valid, because the
  paragraph binds, so `check` does not warn on it.

**This is this project's reading of the judgment and of the rule's
amendatory instructions. It is not legal advice.** Check the judgment
yourself before relying on it. The set was identified by two independent
instruments, which agree: eCFR's text on 2024-04-25 compared with the pinned
text, paragraph by paragraph, and the rule's own amendatory instructions
(ba, on #409). §164.520's other 2024 changes are kept, because the judgment
keeps them.

When eCFR removes the vacated text, `etl-hipaa-privacy` refuses to write,
because the vacated set it finds is no longer the pinned 45 and 14. On the
scheduled drift job, that red is the job working: re-pin by hand, and
update this README.

## What the catalog holds

**18 sections carrying 863 paragraphs**, §§164.500–535, with §164.501
(Definitions) excluded, as the Security Rule's catalog excludes its own.
Each section is a control, and each paragraph is an enhancement under the
citation **eCFR itself gives it** (`164.502(a)(5)(i)(A)(1)`). Nothing is
derived from the paragraph markers. On this subpart, the Security Rule's
parser mis-cites or merges 118 of 793 paragraphs, two of them onto real
paragraph ids.

- A section's unnumbered opening sentence is its statement, never dropped.
  §164.510 and §164.512 open with the condition that scopes every paragraph
  beneath them.
- A heading-only paragraph ("(c) Standard: …", whose content is its
  children) is not emitted, nor is a `[Reserved]` one. A citation to one
  still resolves to its section.
- A term-keyed definition inside a section (§164.504(a)) is not a
  requirement and is not emitted.

The counts are pinned in `policyforge/ingest/hipaa_privacy.py` and checked
by `tests/test_hipaa_privacy.py`.

## No published mapping to 800-53

NIST's OLIR and CPRT catalogs were enumerated (this project's research, #409), and
every HIPAA entry in them maps the **Security** Rule. HHS and third parties
were not searched. The Privacy Rule governs uses and disclosures, not
security controls, and placing it against 800-53 without a source's mapping
would assert something no source asserts. So this catalog is **not
anchored**, is not counted in `/coverage`'s 800-53 figures, and reads there
as "no published crosswalk found", as 42 CFR Part 2 does. A document citing
it is reached by `drift` through its own ids, with the section as the unit
(`family: section`).

## Source

|          |                                                                                              |
| -------- | -------------------------------------------------------------------------------------------- |
| Text     | eCFR renderer, Title 45 Part 164 Subpart E, point-in-time **2026-09-17**                     |
| Guard    | eCFR versioner XML for the same date: each section's words must equal the renderer's         |
| Pre-rule | eCFR renderer, Subpart E, point-in-time **2024-04-25**, quoted for the 14 revised units only |
| Licence  | Public domain (a US federal regulation)                                                      |
| Command  | `policyforge etl-hipaa-privacy` (writes this catalog and the Breach Notification Rule's)     |

eCFR is an editorial compilation of the CFR, not its official legal
edition. That is our understanding, not a measurement, because eCFR's own
statement couldn't be fetched verbatim.

The catalog is pinned to a date, not to file hashes. eCFR serves each
date's text as it stood, and its HTML names the date in every link, so a
new date is always new bytes. What refuses is the content: the extent, the
vacated set by name, and the renderer's words against the XML's.

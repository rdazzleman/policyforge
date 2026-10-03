# Substance Use Disorder Patient Records (42 CFR Part 2)

Public domain (US federal regulation — 42 CFR Part 2, made under 42 U.S.C.
290dd-2, the confidentiality statute for substance use disorder treatment
records). Same basis as NIST 800-53/FedRAMP/ARC-AMPE/HIPAA, so unlike
HITRUST/GovRAMP this is safe to bundle directly rather than treat as BYOC.

## How to cite it

In a document, write **`[Substance Use Disorder Records 2.16(a)]`**.

The catalog declares itself as `Substance Use Disorder Records` rather than
as `42 CFR Part 2`. A framework name beginning with a digit is not a source
tag, so `[42 CFR Part 2 2.16(a)]` was prose — a policy tagged that way
reported *no citations* and passed `satisfies --strict` with exit 0. The
four-word name is deliberate over `SUD Records`: the reader a citation
exists for is an assessor, who may not work in behavioural health and may
not expand the acronym.

## Two sections of thirty-eight, and that is the whole catalog

**The thinness is the correct answer, not a parse failure.** Part 2 has 38
sections. Two of them are here:

| Control | Title                                             |
| ------- | ------------------------------------------------- |
| `2.16`  | Security for records and notification of breaches |
| `2.19`  | Disposition of records by discontinued programs   |

Most of Part 2 is **conduct**, not controls. It says when a disclosure is
permitted, what a consent must contain, and what a court must find before
it orders a record produced. 800-53 says what to implement; those sections
say when an act is lawful. Citing `2.66` asserts that a court **may
authorise a disclosure** — it does not assert that a safeguard exists, and
it is not evidence that one does.

If you are here because the count looked wrong, the count is the answer to
a question someone already asked. Read on rather than re-running the
parser.

## The test

> **Does this section impose an obligation that would not exist without
> it?** If yes, it is a control. If it only says *apply § 2.16 to this
> population*, it is a requirement of § 2.16.

Applied mechanically rather than by judgement, so anyone can re-run it: an
obligation is independent if the section is **the only place in the part
where the machinery appears**, which is a set difference against § 2.16's
own text.

## What it rejected, and why that matters more than what it kept

A sceptical assessor does not ask why the court-order section is missing.
They ask why the one that talks about destroying records is missing. So
both are named here with the test output beside them.

| Section | Terms absent from § 2.16 | Refs to § 2.16 | Verdict                 |
| ------- | ------------------------ | -------------- | ----------------------- |
| `2.19`  | 13                       | 4              | **control** — kept      |
| `2.52`  | 0                        | 2              | requirement of § 2.16   |
| `2.53`  | 0                        | 2              | requirement of § 2.16   |
| `2.66`  | 1 (false positive)       | 1              | conduct — a court order |

**The test surfaces candidates; reading decides.** § 2.66's one term is
`sealed`, and in context it is *"the court has ordered the record of the
proceeding **sealed from public scrutiny**"* — a judicial seal, not a
sealed container. Its single reference to § 2.16 is *"Secure the records in
accordance with § 2.16"*, which is derivative, so § 2.66 is conduct for the
reason its heading suggests.

That false positive is left in the table rather than tidied out, because a
mechanical test presented as decisive is the failure this catalog exists to
avoid. Anyone extending it to another part should expect the same: the set
difference tells you where to look, and only the text tells you what you
found.

**`2.52` (scientific research) is the hard case.** It genuinely says
*"Must maintain and destroy patient identifying information in accordance
with the security policies and procedures established under § 2.16"* — it
uses the word sanitization, and it imposes a real duty on researchers. It
is still not a control, because every piece of machinery it names § 2.16
already has. It says *apply § 2.16 to researchers*. Emitting it separately
would crosswalk one requirement twice and inflate the catalog with an
entry that adds no obligation. `2.53` (audits and evaluations) is the same
shape.

**`2.66` is the clear case**, and it is what the every-section-is-a-control
reading would have produced: an entry asserting a control exists because a
court may order a disclosure.

## Why § 2.19 is here, when its heading does not suggest it

"Disposition of records by discontinued programs" reads administrative, and
its **(a) General** paragraph really is derivative — § 2.16's policies
applied at shutdown. **(b) Special procedure where retention period
required by law is a different regime**, and it is where the controls live:

- a portable electronic device with **implemented encryption at rest**, and
  a **backup copy on separate media**, both encrypted
- **decryption tools stored separately from the data**, with the
  responsible person on the access control list
- sealed containers with a prescribed label naming the legal authority and
  the expiry date, held in a **climate-controlled** environment
- all hard-copy media — printer and facsimile ribbons, drums — sanitized to
  non-retrievable
- **within one year** of discontinuation, the original electronic media
  sanitized

None of that appears in § 2.16. Key separation, encryption at rest, backup
and a retention deadline exist **only** in § 2.19, which is also the most
crosswalkable material in the part.

The general/special split is worth knowing if you extend this: a reader
asking *"is this derivative?"* gets a correct **yes** from the paragraph
that answers it and never reaches the one that does not. The test is
applied to a section's **full text**, never to a quoted clause.

## What was parsed

2 sections carrying 4 requirements. A section is a control; its top-level
lettered paragraphs are requirements, carried as enhancements. Deeper
nesting — `(a)(1)(i)(A)` under § 2.16 — stays inside the requirement it
qualifies, because those paragraphs say what the formal policies must
address rather than imposing separate duties. This is the same split
`hipaa-security-rule` makes between a Standard and its implementation
specifications, and `cfr-171-information-blocking` after it.

**Nothing emitted is empty, at any level.** § 2.19(b)(1)(i)(B) is
`[Reserved]`. It is omitted from the requirements and its absence is
asserted by two tests — one pinning it present in the source, one pinning
it absent from the emitted text. Part 2 has **no** reserved sections and
three reserved paragraphs (`2.19(b)(1)(i)(B)`, `2.63(b)`, `2.68(b)`), so
the paragraph case is the only form it takes here. It is also the worse
one: an empty entry sitting alone in a catalog is conspicuous, while one
folded into a real control's requirements inherits that control's
credibility.

## Currency

The February 2024 alignment rule — which rewrote Part 2 to track HIPAA —
**is** in this text. § 2.16 closes with its own citation, `[89 FR 12622, Feb. 16, 2024]`.

It did not rewrite all of it:

| Effective date    | Sections |
| ----------------- | -------- |
| Jan. 18, 2017     | 20       |
| **Feb. 16, 2024** | **14**   |
| Mar. 16, 2024     | 1        |
| July 15, 2020     | 1        |

Worth knowing, because "Part 2 was updated in 2024" is true and misleading
about the other twenty sections.

## Regenerating it

Source: eCFR's public versioner API,
<https://www.ecfr.gov/api/versioner/v1/full/%7Bdate%7D/title-42.xml?part=2>
— not a hand-copied transcription, so this can always be regenerated and
diffed against the actual current regulation text:

```
policyforge etl-part2
```

`framework.yaml` records the effective date fetched, the exact URL, and the
SHA-256 of the catalog produced. eCFR has no release tags; the effective
date is the only thing that names a revision. The monthly `framework-drift`
job re-runs this and fails the build if the regulation has moved.

See `ingest/part2_loader.py` for parsing details — in particular the
section-id pattern. Part 2 numbers its sections `2.1` to `2.68`, one or two
digits after the dot; Part 171's `^171\.\d{3,4}$` matches **zero** of them,
so a parser adapted from that one produces an empty catalog with nothing
raising. The loader refuses a parse that matches fewer sections than the
document contains, and names the ones it missed.

## Crosswalk

**This catalog seeds normally.** Unlike `cfr-171-information-blocking`, it
is not in `NOT_CROSSWALK_ANCHORABLE` — § 2.16 and § 2.19 state safeguards
to implement, so there is something for an 800-53 control to correspond to.
Media sanitization, encryption at rest, key separation, backups and
retention all have counterparts.

It ships without a `source_crosswalk`, which is a different thing from
being unmappable. NIST publishes an official HIPAA-to-800-53 crosswalk that
this project ingests (`etl-hipaa-crosswalk`). **No equivalent mapping for
Part 2 was found.** A crosswalk invented here would be this project's
opinion wearing the source's authority, so the mapping is left to
`crosswalk propose` and a human reviewer, where its provenance is visible.

**The HIPAA crosswalk does not reach this catalog indirectly.** § 2.16 cites
three HIPAA provisions: 45 CFR 164.514(b) (de-identification), and 45 CFR
part 160 with part 164 Subpart D (general administrative rules and breach
notification). § 2.19 cites none. The published HIPAA mappings, NIST SP
800-66r2 and the OCR crosswalk, map the Security Rule, which is part 164
Subpart C. Neither section this catalog ships cites anything in Subpart
C, so nothing here reaches 800-53 through them. The rest of Part 2 was
not read for this; only the two shipped sections were.

**What "not found" rests on** (searched 2026-09-24 UTC, #264; NIST OLIR and
CPRT enumerated 2026-09-26 UTC, #279):

- the § 2.16 text, and the 2020 and 2024 Part 2 final rules
- the HITRUST CSF v11.7.0 list of authoritative sources
- NIST SP 800-66r2, and site-scoped searches of nist.gov, csrc.nist.gov,
  hhs.gov, samhsa.gov, healthit.gov and 405d.hhs.gov
- **NIST OLIR, listed in full:** all 102 informative references, of every
  status (Final, Draft, Archive, Work-in-progress). None matches 42 CFR
  Part 2 (the search pattern was first tested against Part 2's real title).
- **NIST CPRT, listed in full:** all 197 frameworks (211 versions listed;
  5 frameworks list no version).
  None is 42 CFR Part 2, and the only health datasets are the four HIPAA
  Security Rule mappings, whose source is 45 CFR 164, not Title 42.

**Not ruled out:** OLIR and CPRT were listed through the undocumented
endpoints NIST's own pages call, and what was searched is each catalog's
metadata, not every dataset's individual elements. The snapshots' hashes
are on #279, so a later run can check whether the catalogs changed. The OCR
crosswalk's contents were not readable. Commercial frameworks were not checked. If a
mapping turns up, Part 2 moves to the same footing as 800-171 (see its
README).

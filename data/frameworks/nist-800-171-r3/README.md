# NIST SP 800-171 Rev 3

Public domain (a US government work — NIST Special Publication 800-171
Revision 3, *Protecting Controlled Unclassified Information in Nonfederal
Systems and Organizations*). Same basis as NIST 800-53/FedRAMP/ARC-AMPE,
so unlike HITRUST/GovRAMP this is safe to bundle directly rather than
treat as BYOC.

## Read this first if you are here because of CMMC

**This is Revision 3. CMMC Level 2 currently assesses against R2.** Those
are different revisions of the same publication, and their identifiers are
shaped differently:

|                         |            |
| ----------------------- | ---------- |
| Rev 3 (this catalog)    | `03.01.01` |
| R2 (what CMMC assesses) | `3.1.1`    |

So **a citation to this catalog is a citation to rev 3**, and it is not
interchangeable with an R2 citation in an assessment. Rev 3 is what NIST
publishes machine-readable, which is why it is what this project can
ingest; it is not a claim that rev 3 is what your assessor will use.

If you are preparing for a CMMC Level 2 assessment, check which revision
your assessment is against before citing anything here. A buyer usually
adopts 800-171 *because* of CMMC, and should meet that sentence here
rather than in the assessment.

## What was parsed

97 requirements across 17 families, from NIST's OSCAL edition
(`metadata.version` 1.1.0).

**33 withdrawn requirements are excluded**, the same treatment 800-53's
withdrawn enhancements get: they carry no statement text, they are not
part of rev 3 any more, and listing them would invite implementation
narratives for requirements that no longer exist. The count is reported
each time `etl-800-171` runs rather than dropped silently — 130 in the
publication, 97 live, 33 withdrawn.

**There are no enhancements, and that is the source's shape rather than a
parse that came up short.** 800-171 rev 3 is structurally flat: no
requirement nests another, verified across all 130. Where 800-53 has
`AC-2(1)`, rev 3 has `03.01.01` with lettered items inside its statement.

**There are no baselines either.** 800-53 ships Low/Moderate/High profiles
alongside its catalog; `nist.gov/SP800-171/rev3/json/` contains the catalog
and nothing else. So every requirement's `baseline` is empty, and
`ssp --baseline` has nothing to select on for this framework. That empty
is NIST's, not ours.

## Identifiers, and why they took work

NIST's own catalogs disagree about where the citation lives, and reading
this one with 800-53's rules **does not fail** — it produces 97 well-formed
requirements whose ids are sentences:

```
800-53   label props: "AC-01" (zero-padded), "AC-1" (unclassed), "AC-01" (800-53A)
         -> the unclassed one IS the citation

800-171  label props: "Account Management (03.01.01)"   -- the only one
         -> the unclassed one is the TITLE plus the citation
```

Read the 800-53 way, `control_id` comes out as `Account Management (03.01.01)`: countable, renderable, crosswalkable, and not something
anyone would cite. Every count check passes on that catalog. The real
identifier is the `sort-id` prop, which is what this catalog uses.

`tests/test_oscal_800_171.py` pins the *shape* — `^\d{2}\.\d{2}\.\d{2}$`
for a requirement and `^\d{2}\.\d{2}$` for a family — because shape is the
only thing that separates this catalog from the well-formed wrong one.

**Statement lettering renders as `a` here and `a.` in 800-53.** That is
NIST's difference carried through, not an inconsistency to fix: 800-53's
label prop contains the dot and 800-171's does not. Sub-item labels are
published as `SR-03.01.01.a` — the requirement's own id, then the position
— and the repeated id is stripped, leaving `a`. Measured before relying on
it: 252 part labels across every live requirement, 252 matching
`<prefix><id>.<segment>`, one prefix (`SR-`), zero exceptions. A label that
does not fit that shape is kept exactly as published.

## Citing it alongside 800-53

**Write the framework name in full.** With two NIST-family catalogs loaded,
a bare `NIST` no longer names one of them:

```
[NIST AC-2]            unresolved -- names both nist-800-53 and nist-800-171
[NIST 800-53 AC-2]     resolves
[NIST 800-171 03.01.01] resolves
```

That is `topics/satisfies.resolve_framework` declining to guess rather than
a defect: an abbreviation resolves when it names exactly one loaded
catalog, and accepting one that matches two would silently pick a catalog
on the reader's behalf.

**This is a consequence of loading both, not of this catalog existing.**
Bundling is not loading: every command that resolves citations takes the
catalogs to load as a required option, and `map`'s default is 800-53 by
itself. A document citing `[NIST AC-2]` keeps resolving until someone
passes both catalogs in one run — at which point replace it with
`[NIST 800-53 AC-2]`, or regenerate.

## What changes in the zardoz shell

**Nothing, now.** Installing this catalog does not move the bare
`/coverage` numbers, and it used to.

The shell hands the catalogs on disk to the coverage report, and that
report's scope is the NIST 800-53 set the topic registry anchors to.
Until recently every catalog went in as that scope, so a HIPAA or CFR
requirement was an orphan the moment its catalog was installed — no topic
anchors to its identifiers — and the denominator grew while the numerator
did not. Installing 800-171 moved the headline from 49% to 46% with
nothing about the programme changing.

Now the non-NIST catalogs are reported where they belong, through the
crosswalk:

```
NIST-800-171 reachable via the crosswalk
  97 of 97 requirements map to an owned NIST control
```

That is the example topic registry (`config/topics.example.yaml`),
measured on 2026-09-24; yours depends on which 800-53 controls your
topics own. **The mapping is NIST's own.** NIST's OSCAL file for rev 3,
the one this catalog is built from, links every one of the 97
requirements to the 800-53 controls it was derived from: 157 links, 114
to controls and 43 to enhancements. Since #259 this catalog carries them,
so 800-171 reaches 800-53 through NIST's mapping. Before #259 it did not
read them, and this line read `0 of 97`. **Do not hand-map them with
`crosswalk seed`**: the source asserts the mapping, and a hand-made one
would compete with it.

## Regenerating it

Source is the `usnistgov/oscal-content` repository, pinned to the same
release tag as 800-53:

```
policyforge etl-800-171
```

`framework.yaml` records the OSCAL `metadata.version` fetched, the exact
URL, and the SHA-256 of the catalog produced. The monthly `framework-drift`
job re-runs this and fails the build if the publication has moved.

The URL is written out as a literal rather than templated, and so is
800-53's, because **NIST spells the two differently — including inside a
single URL**: the 800-53 directory is `SP800-53` while the file in it is
`NIST_SP-800-53_rev5_catalog.json`. 800-171 uses `SP800-171` in both. No
template produces both; only literals do.

## Crosswalk

**Every requirement carries NIST's own mapping to 800-53** as its
`source_crosswalk` (#259): 97 of 97 requirements, 157 links. Each rev 3
requirement links to its 800-53 source controls as `rel="reference"`
entries in the catalog's back-matter, **in the same list as its
literature references** (`IR 7874`, `SP 800-63-3`, `FIPS 199`: 199 of
them). In the OSCAL, the title's shape is the only thing that tells a
control from a publication, so `oscal_loader` keeps 800-53-shaped
titles, skips publications, and refuses a title that is neither.

NIST writes the ids zero-padded (`AC-02(03)`) and the 800-53 catalog
does not (`AC-2(3)`), so they are normalised. `etl-800-171` refuses to
write unless every one resolves to an 800-53 control or enhancement.
Read as written, only 22 of the 157 would resolve; keyed on control ids
alone, 114. Both are partial mappings that look like working ones.

The framework key is `nist-800-171`, which
`mapping/crosswalk.FRAMEWORK_ALIASES` already carried before this catalog
existed. A topic cannot anchor an 800-171 requirement; it anchors the
800-53 controls the requirement maps to.

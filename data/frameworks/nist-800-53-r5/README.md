# NIST 800-53 Rev 5

Public domain (US federal government work).

Populated: **300 controls and 714 enhancements**, tagged with the
Low/Moderate/High baselines, from NIST's own OSCAL release of SP 800-53.

## Source

NIST's official OSCAL content repository,
<https://github.com/usnistgov/oscal-content> — the machine-readable edition
of the catalog NIST publishes alongside the PDF:

|                 |                                                                     |
| --------------- | ------------------------------------------------------------------- |
| Catalog         | `nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json`      |
| Baselines       | the `LOW-`/`MODERATE-`/`HIGH-baseline_profile.json` files beside it |
| Catalog version | 5.2.0 (OSCAL 1.2.2)                                                 |

Fetched and parsed with:

```
policyforge etl-oscal
```

Because it comes from NIST directly rather than a hand-copied
transcription, this can always be regenerated and diffed against what NIST
currently publishes. `--no-baselines` skips the profile fetch, at the cost
of losing the baseline tagging that `policyforge ssp --baseline` needs.

## Alternative source

`policyforge etl-vault` parses the same controls out of an Obsidian vault's
markdown notes (see `ingest/nist_vault_loader.py`). That path only works if
you already have a vault in that specific shape; `etl-oscal` needs nothing
but network access, so it is the better default.

## Parsing notes

See `ingest/oscal_loader.py` for the full detail. The parts worth knowing:

- **Identifiers** are the unpadded form NIST prints (`AC-2`, `AC-2(1)`), not
  OSCAL's internal `ac-2`/`ac-2.1` ids and not the zero-padded `AC-02` label
  that also appears in the source. This is the form `mapping/crosswalk.py`
  matches, so getting it wrong silently breaks every crosswalk join.
- **Control text** is reassembled from OSCAL's `parts` tree with its a./b./c.
  lettering intact, since an SSP's implementation narrative is written and
  assessed part-by-part against that lettering.
- **Parameters** are rendered the way SP 800-53 prints them —
  `[Assignment: organization-defined frequency]`,
  `[Selection (one or more): ...]` — rather than left as OSCAL
  `{{ insert: param, ... }}` placeholders. Two edge cases are handled and
  regression-tested: selection choices that themselves contain parameter
  references (AC-7), and a parameter defined on a *sibling* enhancement
  (SC-42(2) references SC-42(1)'s).
- **Withdrawn controls** (182 of them in Rev 5.2.0) are excluded. They
  appear in no baseline and carry no statement text, and listing them would
  invite implementation narratives for controls that no longer exist. The
  count is reported by `etl-oscal` rather than silently dropped.

## Verification

`tests/test_oscal_loader.py` checks the parser against a verbatim excerpt of
the real catalog, and asserts three invariants over the generated file:
every identifier is well-formed, no OSCAL placeholder survives, and the
Low/Moderate/High baselines select exactly **149 / 287 / 370** items —
matching the totals in NIST's published profiles.

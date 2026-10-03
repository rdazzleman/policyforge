# FedRAMP

Public domain (US federal program). Bundled here, built by:

```bash
policyforge etl-oscal     # FedRAMP tailors 800-53; this provides the text
policyforge etl-fedramp
```

Source: [`fedramp-consolidated-rules.json`][rules] in `FedRAMP/rules`, which
that repository's README calls its canonical rules dataset.

## Read this before using it: there is no baseline here

Nothing in `controls.json` says which controls a Low, Moderate or High
system must implement. `baseline` is empty on every control, deliberately.

FedRAMP's machine-readable Low/Moderate/High selection was published as
OSCAL profiles in `GSA/fedramp-automation`. That repository no longer
exists — not archived, not moved; the URL and the GitHub API both return
404 — and no official machine-readable replacement has been published. A
baseline reassembled from an unofficial mirror would be worse than none:
this project's value is that a citation traces to the body that issued it,
and a mirror traces to whoever made the copy.

So `ssp --baseline` and `coverage` will find nothing to filter on here. Use
the 800-53 baselines for that (`etl-oscal` fetches the Low/Moderate/High
profiles) and treat this catalog as what it is — FedRAMP's tailoring of the
controls it speaks to.

## What it does carry

The two things a profile adds to the catalog it profiles, for the 79
controls FedRAMP tailors:

- **Parameter values** FedRAMP has already decided, keyed by the OSCAL
  parameter id the 800-53 prose carries — `ac-06.01_odp.02` is "all
  functions not publicly accessible". 19 of them. These are answers to
  questions `policyforge parameters` would otherwise put to you.
- **Guidance** FedRAMP layers on top, verbatim. 64 controls carry it.

Both are joined onto the 800-53 text they tailor, because the rules dataset
carries no control text of its own — `AC-06-01` is a bare key. That is also
why `etl-fedramp` needs `etl-oscal` to have run first, and why it fails with
an explanation rather than writing a catalog of empty controls.

## Certification classes

Two controls (IA-5, SA-9(5)) read differently depending on FedRAMP's
**Certification Class** — A through D, increasing assurance. That is the
2026 successor to the Low/Moderate/High axis and it is not the same thing:
a class is a category of assurance, not a FIPS 199 categorisation. It is
also not a selection — it varies what a control *says*, not whether it
applies — so it is rendered into the guidance and parameter values under a
heading naming the class, rather than into `baseline`.

See `src/policyforge/ingest/fedramp.py` for the long form.

[rules]: https://github.com/FedRAMP/rules

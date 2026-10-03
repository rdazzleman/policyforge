# GovRAMP

**Not bundled here.** GovRAMP's Terms & Conditions claim ownership of
"documents, downloadable files" published on their site, and no
redistribution license was found. Treat GovRAMP as BYOC (like HITRUST)
until GovRAMP grants explicit permission. The licensing notes are in the
guides at [zardoz.io](https://zardoz.io).

Bring your own matrix instead. `etl-govramp` is part of the
licensed-framework plugin, which is distributed separately from the public
PolicyForge. Without it, the command says so and points you to
`generate-parser`, which can draft a loader for your own matrix.

```bash
policyforge etl-govramp --export local_content/govramp/GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx
```

`local_content/` is gitignored. The command parses in memory and prints what
it found; it writes nothing unless you pass `--out`.

If you email GovRAMP for permission (info@govramp.org) and they grant it,
this is the directory to populate afterward.

## If your own repository may hold it

Same arrangement as HITRUST. Your own use of the matrix very likely permits
your *private* repository to carry it, even though this public one cannot.
That is a decision only the repository owner can make, so it is declared
rather than assumed — in your `config.yaml`:

```yaml
frameworks:
  allow_licensed_in_repo: true    # our reading of GovRAMP's terms permits this
```

Then write the parsed catalog into your own tree:

```bash
policyforge etl-govramp \
  --export local_content/govramp/GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx \
  --out frameworks/govramp/controls.json
```

and declare it beside the data, so `policyforge check` knows what it is:

```yaml
# frameworks/govramp/framework.yaml
id: govramp
name: GovRAMP Rev 5 (Moderate)
licence: licensed
source: GovRAMP controls matrix, published workbook
version: Rev 5 (V1.06)
```

Without that declaration a framework directory is *treated* as licensed
anyway — assuming content is freely redistributable because nobody said
otherwise is the failure mode with consequences.

## Which file to export

There is nothing to export: GovRAMP publishes the matrix as a workbook, and
`etl-govramp` reads it as published. Hand it the `.xlsx` (or `.xlsm`) whole —
the controls sheet is found among the template's other thirteen by its
header captions, so the Low, Moderate and High workbooks all read without
being told which they are, and a renumbered sheet in a later revision needs
no code change.

Two things are worth knowing about the file:

- **It is an SSP template, not a data export.** Most of its sheets are blank
  grids for a service provider to fill in. Only the controls sheet is read;
  the Control Implementation Summary and Control Responsibility Matrix carry
  nothing but zeros in a fresh template and are deliberately not ingested.
- **The impact level and revision come from the cover sheet**, falling back
  to the filename. Both are the first things edited once somebody starts
  working in the template, so `--impact-level` and `--version` override them.

If detection fails on your workbook's shape it will say which columns it
could not find, and
`policyforge generate-parser --framework govramp --sample <path>` drafts a
loader for that specific file.

## What it looks like once parsed

The long form is in the guides at [zardoz.io](https://zardoz.io). In short, GovRAMP is a profile
over 800-53 rather than a catalog of its own, carrying the parameter values
it has already decided and the requirements it layers on top, across three
verification tiers — Core, Ready, Authorized — which nest inside each other
and are *not* the same axis as the Low/Moderate/High impact level of the
workbook they live in.

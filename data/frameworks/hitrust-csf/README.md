# HITRUST CSF

**Not bundled here, and never will be.** HITRUST CSF is licensed content:
its requirement text and its mappings can't be redistributed, so no
open-source project can ship them. This directory holds no `controls.json`
and no `framework.yaml` on purpose — a manifest here would make the licence
check report a licensed catalog committed to a public repository, which is
exactly the thing it exists to catch.

Bring your own export instead. `etl-hitrust` is part of the
licensed-framework plugin, which is distributed separately from the public
PolicyForge. Without it, the command says so and points you to
`generate-parser`, which can draft a loader for your own export.

```bash
policyforge etl-hitrust --export local_content/hitrust/CSFLibraryReport.csv
```

`local_content/` is gitignored. The command parses in memory and prints what
it found; it writes nothing unless you pass `--out`.

## If your own repository may hold it

Your MyCSF licence very likely permits your *private* repository to carry
the export, even though this public one cannot. That is a decision only the
repository owner can make, so it is declared rather than assumed — in your
`config.yaml`:

```yaml
frameworks:
  allow_licensed_in_repo: true    # our MyCSF licence permits this
```

Then write the parsed catalog into your own tree, next to your `docs/`:

```bash
policyforge etl-hitrust \
  --export local_content/hitrust/CSFLibraryReport.csv \
  --out frameworks/hitrust-csf/controls.json
```

and declare it beside the data, so `policyforge check` knows what it is:

```yaml
# frameworks/hitrust-csf/framework.yaml
id: hitrust-csf
name: HITRUST CSF v11.7
licence: licensed
source: MyCSF library export, under our own licence
version: v11.7
```

Without that declaration a framework directory is *treated* as licensed
anyway — assuming content is freely redistributable because nobody said
otherwise is the failure mode with consequences.

## Which file to export

MyCSF renders its library report through SQL Server Reporting Services, and
the renderings are not equivalent. `etl-hitrust` reads `.csv`, `.tsv`,
`.xlsx`, `.xlsm`, `.html`, `.htm`, `.mhtml` and `.mht`, but:

**Prefer the CSV.** The CSV carries a mapping list on nearly every
requirement statement. The MHTML of the same report carries markedly fewer
mapping blocks, because the rendered layout suppresses repeats the data
export keeps. The mappings are
most of the value — they are HITRUST's own reconciliation against NIST
800-53, HIPAA, ARC-AMPE and FedRAMP.

Two things about a CSV export are worth expecting:

- Its column headers are SSRS textbox names (`Textbox52`, `Textbox105`) and
  identify nothing. Columns are recognised by their caption columns and by
  the shape of their values instead.
- It repeats whole rows, as an artifact of a join the report does not
  render. They are collapsed on the way in.

If detection fails on your export's shape it will say which fields it could
not find, and `policyforge generate-parser --framework hitrust --sample <path>` drafts a loader for that specific file.

## What it looks like once parsed

The long form is in the guides at [zardoz.io](https://zardoz.io). In short: four tiers (Category → Objective → Control Reference →
Requirement), levels that split into a 1/2/3 maturity ladder and many
regulatory overlays selected by scoping factors, and a per-requirement
crosswalk into dozens of authoritative sources.

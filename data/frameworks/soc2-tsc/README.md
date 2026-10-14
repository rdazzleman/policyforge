# SOC 2: AICPA Trust Services Criteria (bring your own)

**Nothing of the AICPA's ships here.** The Trust Services Criteria (TSC)
are published by the AICPA under its terms. This directory holds only this
README. `policyforge etl-soc2-tsc` builds a catalog from **your own copy**,
on your machine, into `local_content/`, which git ignores. This project
fetches nothing from the AICPA, and no copy of the criteria was used to
build or test it.

`etl-soc2-tsc` is part of the licensed-framework plugin, which is distributed
separately from the public PolicyForge (to ask for it, email [info@zardoz.io](mailto:info@zardoz.io?subject=PolicyForge%20licensed-framework%20plugin)). Without it, the command says so.
`generate-parser` doesn't draft SOC 2 loaders.

## The AICPA's terms govern your copy

Whether these terms permit parsing your copy into a catalog, or sending it
to a model, is **your licensing decision**. This project doesn't interpret
them for you. The site's Terms & Conditions, which govern the download, are
at <https://www.aicpa-cima.com/help/terms-and-conditions> ("Version 4").
Verbatim, as this project's research read them on 2026-09-26:

> "You may download material displayed on the Website for non-commercial,
> personal use provided you also retain all copyright and other proprietary
> notices contained on the materials and you may not modify or create
> derivative works of the material."

> "You may not distribute, modify, transmit, reuse, repost or use the
> Website Content for public or commercial purposes, including the text and
> images, without our written permission."

> "We do not consent and specifically object to use of this website,
> including any and all content, to train artificial intelligence (AI)
> platforms or machine learning algorithms and to inclusion of content from
> this website in the knowledge base of Large Language Models (LLMs) and
> similar AI platforms."

The criteria PDF carries its own copyright notice, which no one here has
read. The AICPA's permissions address, as its site gives it:
`copyright-permissions@aicpa-cima.com`.

## Which models it reaches

The catalog is written as `licensed` content, in `local_content/` with a
`framework.yaml` saying `licence: licensed`, and the file itself carries
`"licence": "licensed"`, so it stays licensed if it's moved or copied
elsewhere. Under the default boundary,
licensed content reaches only local models, and `synthesize` refuses a
hosted provider for it. Your boundary configuration governs what happens
beyond that (the guides at [zardoz.io](https://zardoz.io) explain which content may reach which model).
`etl-soc2-tsc` refuses any `--out` where that classification wouldn't apply.

## Making the input

1. **The criteria, as plain text.** The AICPA publishes the criteria as a
   PDF. Export its text with a tool of your choice, and save it in
   `local_content/`. This project deliberately has no PDF parser: it never
   parses the licensed document itself. Each criterion must start a line
   with its id (`CC1.1`, `A1.2`, `PI1.3`, `C1.1`, `P1.1`). The lines after it,
   up to the next id, are kept as its points of focus.
1. **The mapping to NIST SP 800-53, optional.** Your own copy of the AICPA's
   TSC-to-800-53 workbook (.xlsx). The sheet holding it needs one column
   headed with "TSC" (or "Trust Services", or "Criteria") and one headed
   with "800-53". A cell may list several ids.

```bash
policyforge etl-soc2-tsc \
  --criteria local_content/tsc-criteria.txt \
  --mapping local_content/tsc_to_nist_800-53.xlsx \
  --out local_content/soc2-tsc/controls.json
```

## Your run is the first real one

This ETL was built and tested only against **synthetic** criteria and
mappings: invented text in the TSC's shape. So its grammar is an
assumption. It refuses the **whole** file, writing nothing, whenever it can
see a criterion it didn't parse: every id-shaped token in the text must be
a criterion it read (a criterion listed as a bullet, in a table, after a
page number or in lowercase would otherwise be merged into the one before).
It **cannot** see a criterion whose id is missing from your export
altogether, so check the counts it reports against your copy. It refuses a line that starts like an id but isn't
one, a repeated id, a criterion with no text, and a gap in a group's
numbering (`CC1.1`, `CC1.3` with no `CC1.2`). It reports the counts your file
yields. It can't check them against a published number, because this
project holds none. If it refuses a file you believe is right, the refusal
names the line; please report the shape (not the text).

## The mapping predates Rev 5, and says so

The AICPA dates its TSC-to-800-53 mapping **2020-01-22**, before SP 800-53
Rev 5 final. This project ships Rev 5. So:

- **Every link reads as partial** (`crosswalk_relationship: source-untyped`
  in your `framework.yaml`). The mapping states no relationship, so whether
  a control covers a criterion is a person's call. `/coverage` names the
  source and its date.
- **Each 800-53 id NIST marks as changed substantively since Rev 4** is
  flagged *"changed between Rev 4 and Rev 5 (NIST)"* and listed under
  `crosswalk_changed_since_rev4`. That uses NIST's own comparison workbook,
  its "More than editorial or administrative change?" column, pinned by
  hash and fetched from NIST (or pass `--nist-comparison` with a saved
  copy).
- **An id withdrawn in Rev 5, or not a Rev 5 id at all, is refused and
  named** (`crosswalk_refused`). Nothing is remapped.

A criterion with no link in the mapping is reported as **not mapped by the
source**, which is not the same as having no 800-53 equivalent.

**PolicyForge reads one criterion per row.** A criterion cell holds one
criterion id, optionally followed by its text (`CC6.1 The entity ...`).
A row whose cell lists several (`CC6.1, CC6.2`), or follows its id with
anything that could be another written short (`CC6.1-6.3`, `CC6.1, 6.2`,
`CC6.1-3`, `CC6.1, 2, 3`, `CC6.1 & 2`, `CC6.1 to 3`), refuses the whole
file, naming the row and the cell; split the row in your copy to map
each. Text that merely looks like that (`CC6.1 and 2FA`) is refused too,
so reword it in your copy. Whether such a cell means each criterion, the
pair or a range isn't something this project can tell without the
source, so it doesn't guess. A second reference joined to the first only
by a word this parser doesn't list (`CC6.1 plus 2`), or written with no
number at all (`CC6.1 and CC6 generally`), passes as text, and its links
go to the first criterion alone.

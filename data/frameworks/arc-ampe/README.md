# ARC-AMPE

Public domain (published directly by CMS, a federal agency — no copyright
notice or redistribution restriction found in the source documents).
Bundled here, built by:

```bash
policyforge etl-arc-ampe
```

Source: [ARC-AMPE Volume II SSPP, ACA Administering Entity, v1.02][vol2],
linked from CMS's [Marketplace regulations and guidance page][reg].

## Volume II, not Volume I

The document a search turns up first — [ARC-AMPE Volume I][vol1], a 55-page
PDF — is the narrative: scope, applicability, roles, the relationship to the
ACA AE CSF Profile. **It contains no controls.** Volume I says so itself, in
a footnote it repeats four times: "ARC-AMPE Volume II is the System Security
and Privacy Plan (SSPP) template with required baseline controls."

Volume II is an `.xlsx`, and it is what `etl-arc-ampe` reads.

## What it is

ARC-AMPE — Acceptable Risk Controls for ACA, Medicaid, and Partner Entities
— is CMS's security and privacy framework for Health Insurance Exchanges and
the entities around them. It supersedes and replaces MARS-E and the
Non-Exchange Entity GRC Framework.

The bundled catalog is the **ACA Administering Entity mandatory baseline**:
402 items (215 controls, 187 enhancements), every one required, which is why
`baseline` is set here and left unset for FedRAMP. CMS derives them from
NIST SP 800-53 Rev 5 and numbers them with 800-53 identifiers, so all 402
crosswalk onto their 800-53 equivalents and reach `policyforge map`.

Two things about the text are worth expecting:

- **The control statements arrive already tailored.** Where 800-53 writes
  `[Assignment: organization-defined time period]`, ARC-AMPE writes "within
  twenty-four (24) hours" and "five (5) consecutive invalid logon attempts".
  CMS's decisions are in the prose rather than in a parameter table, so
  unlike a profile there is nothing in `parameter_values` — the values are
  in `control_statement`, where CMS put them.
- **96 of the 402 guidance cells say there is no guidance**, in words
  ("There are no supplemental control requirements and guidance for this
  control"). That is the template speaking, not CMS, so it is dropped rather
  than carried into a generated document as though CMS had written it.

The workbook is an SSPP template, so most of its eight sheets are blank
grids for an entity to fill in. Only the `AE Mandatory Baseline` sheet is
read, and only its first seven columns — the six pairs of "Control
Implementation Description" and "Control Status" to the right are the blanks
and are deliberately not ingested.

## The Direct Enrollment Entity baseline

CMS publishes a second, smaller Volume II for Direct Enrollment Entities
(308 controls). It is distributed through CMS zONE, which requires requested
access, so it is not fetched and is not bundled. If you have it:

```bash
policyforge etl-arc-ampe --export path/to/dee-workbook.xlsx --out ...
```

The sheet is found by its shape rather than its name, so the DEE workbook
reads without the loader being told which one it is.

See `src/policyforge/ingest/arc_ampe.py` for the long form.

[reg]: https://www.cms.gov/marketplace/resources/regulations-guidance
[vol1]: https://www.cms.gov/files/document/arc-ampe-vol-1-v102-508-5cr-04112025.pdf
[vol2]: https://www.cms.gov/files/document/arc-ampevol2sspp-aca-aev102-50803212025.xlsx

# HITRUST AI Security Certification

**Not bundled here, and never will be.** Like the CSF, HITRUST's AI
requirement text and its mappings are licensed content that no
open-source project can redistribute. This directory holds no
`controls.json` and no `framework.yaml` on purpose — a manifest here would
make the licence check report a licensed catalog committed to a public
repository, which is exactly the thing it exists to catch.

## It is an add-on, not a standalone certification

HITRUST AI Security Certification is assessed **as an add-on to a CSF
assessment**. It cannot be held on its own, so an organization carrying it
carries the CSF too — which means this catalog and `hitrust-csf` are
loaded together in the normal case rather than the unusual one.

That is why the key separation below is a real problem rather than an
anticipated one: the two catalogs *will* meet.

## Its key is reserved and separate

```
HITRUST AI Security Certification  ->  hitrust-ai
HITRUST AI                         ->  hitrust-ai
HITRUST CSF                        ->  hitrust
```

Both spellings are pinned in `mapping/crosswalk.FRAMEWORK_ALIASES` and
tested. Without that pin the first-word fallback would key `HITRUST AI` to
`hitrust` alongside the CSF, pooling their requirement ids — so a citation
to one could resolve against the other, silently, in a report whose whole
purpose is that a citation can be followed.

Both names are also legal source tags, so `[HITRUST AI 01.a]` resolves
once a catalog is loaded. That is not automatic: a framework name
beginning with a digit is not a tag at all, which is what made `45 CFR 171` and `42 CFR Part 2` impossible to cite until they were renamed.

## There is no `etl-hitrust-ai` yet

**Do not run `etl-hitrust` on an AI export.** (`etl-hitrust` is part of the
licensed-framework plugin, which is distributed separately from the public
PolicyForge (to ask for it, email [info@zardoz.io](mailto:info@zardoz.io?subject=PolicyForge%20licensed-framework%20plugin)).) That command hardcodes
`FRAMEWORK = "HITRUST-CSF"` and stamps it on whatever it is given, so an
AI export parsed through it arrives labelled as CSF content. Worse, a CSF
export and an AI export both come out under one framework name, and their
requirement ids pool into a single bucket — the collision the alias pin
above exists to prevent, arriving through the loader instead of through
the key.

`generate-parser --framework` accepts `hitrust` and `govramp` only, so it
cannot draft one for this either.

Until a loader exists, parse it yourself and **declare the name beside the
data**, in your own repository rather than this one:

```yaml
# frameworks/hitrust-ai/framework.yaml
id: hitrust-ai
name: HITRUST AI Security Certification
licence: licensed
source: MyCSF export, under our own licence
```

The `name` is what a citation has to match, so write one of the two
spellings above rather than inventing a third.

## If your own repository may hold it

Your MyCSF licence very likely permits your *private* repository to carry
the export, even though this public one cannot. That is a decision only
the repository owner can make, so it is declared rather than assumed — in
your `config.yaml`:

```yaml
frameworks:
  allow_licensed_in_repo: true    # our MyCSF licence permits this
```

Keep the export itself in `local_content/`, which is gitignored, and write
any parsed catalog into your own tree next to your `docs/` — never into
this directory.

## Why this file exists at all

A directory with only a README is how this project says *"we know about
this framework, here is what to do, and the data is not ours to ship."*
`govramp` and `hitrust-csf` are here on the same terms.

It also reserves the name. `hitrust-ai` is invisible to
`test_no_bundled_catalog_changed_key`, which derives its population from
catalogs that have a `controls.json` — a README-only catalog has no
declared name in this tree to check. That test pins its own skipped set
so adding this directory turned it red and the gap was decided rather than
inherited: **the key is covered at the alias level instead**, by
`test_hitrust_ai_does_not_share_a_key_with_the_csf`, which needs no
catalog because the pin is in the alias table.

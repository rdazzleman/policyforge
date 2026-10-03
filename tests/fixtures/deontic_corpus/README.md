# Deontic conservation corpus

128 Standards and Procedures that PolicyForge generated, and what every
block-structure reader in `policyforge.content.deontic` and `grounding`
returned for each of them. `tests/test_deontic_conservation.py` holds the
readers to that record, so a change to how `check` reads a document shows
up row by row, not as a claim (#376).

## What is here

| path                    | what                                                                |
| ----------------------- | ------------------------------------------------------------------- |
| `documents/standards/`  | 83 generated Standards                                              |
| `documents/procedures/` | 45 generated Procedures                                             |
| `baseline.json`         | every reader's output per document, and the commit that recorded it |

Each document is named `<first 12 hex of its SHA-256>-<original name>.md.txt`.
The hash prefix keeps two documents with the same original name apart.

**They are `.md.txt`, not `.md`, on purpose.** They are data this test reads,
not the repository's markdown. The markdown gates (`mdformat` in
`scripts/check.py`, in CI and in pre-commit) select `*.md`, and 38 of the 128
would fail them as generated. Reformatting them would change the very text
the baseline records. Naming them as data leaves the gates' populations
exactly as they were.

They are byte-exact in git (`-text` in `.gitattributes`), so the hashes below
name the same corpus on every platform.

## Which corpus, exactly

SHA-256 over each tier's files, concatenated in name order:

| tier       | documents | set-hash                                                           |
| ---------- | --------: | ------------------------------------------------------------------ |
| Standards  |        83 | `b81107c768ea487d75784967292ad4ff7f41ca921a6e776e3be4778b384c646d` |
| Procedures |        45 | `61941ecff02bd8e9823eee462b82ce4ef9e58215a27d3586fb8a2ed3462a7c72` |

Nothing was dropped. These are all 128 of the saved generated documents that
#363, #226 and #376 measured, deduplicated by SHA-256. The size allowed all of
them: 2.85 MB of documents and 4.07 MB of baseline. The population is the
point, so no coverage trim was needed.

## Where they came from

Model output from PolicyForge's own measurement runs for 1.6.0 and 1.6.1,
over the **bundled public-domain catalogs only**: NIST SP 800-53, FedRAMP,
ARC-AMPE, the HIPAA Security Rule, and the NIST AI RMF and its Playbook. The
example organisation is the fictional **Acme Health**, or **Example Org**.
No HITRUST, GovRAMP or other licensed export was loaded for any of them.

**One is not plain model output:** `standards/*-seeded-governance.md.txt` is
a generated AI Governance Standard with one sentence rewritten by hand for
#301 ("Acme Health must, for Govern 1.1, maintain ..."), as a planted Playbook
violation. It is one of only two documents here where the Playbook check
reports anything, so it stays, named. It was stored with CRLF line endings
and is committed with LF; its text as read is unchanged.
This is 80's condition on #376 for putting generated documents in a public
repository.

`tests/test_deontic_corpus_content.py` checks the content, measured, not
assumed. Each guard is first shown failing on a planted line:

- no licensed catalog's name, ids or distinctive phrases: HITRUST (names,
  CSF versions, `Control Reference:`, level requirements, ids like `01.a`),
  GovRAMP and StateRAMP, SOC 2 (names, `CC6.1`-style criteria), ISO 27001 and
  27002, and PCI DSS. **0 hits over the 128.**
- every citation names a public-domain framework;
- the only organisations named are the two placeholders.

**What that cannot see:** licensed text paraphrased with none of those names,
ids or phrases. The provenance above is the stronger evidence, and it is why
only documents generated from bundled catalogs belong here.

The words "copyright" (6 times) and "licensed" (twice) do appear, all in the
change-configuration Standards. Every one is NIST SP 800-53 CM-10's own
subject (software "used in accordance with contract agreements and copyright
laws"), cited as `[NIST 800-53 CM-10 ...]`.

## Changing the baseline

Only through the script, never a test flag:

```
python scripts/deontic_baseline.py           # compare; print every changed row
python scripts/deontic_baseline.py --write   # the same printout, then write
```

The printout names each changed (document, reader, line). **A PR that changes
`baseline.json` carries that printout**, so a reader rules on each change. A
baseline diff with no printout is changes-requested on sight (1d on #376).
The test compares against the committed file and never recomputes its own
expectation.

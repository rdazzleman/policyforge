# Product security commitments

Eight things PolicyForge commits to, each with the test that fails when it
stops being true.

## Why this document is shaped like this

The rest of this project argues that a control an organization cannot
evidence is not a control. Applying that to ourselves: a security promise in
a README is worth roughly what an unaudited policy document is worth — it
describes an intention, and nothing detects the day it stops being accurate.

So every commitment below names the test that enforces it. A reader who
doubts one can run that test. A contributor who breaks one gets a failing
build naming the commitment, not a code review that might catch it.

Three of these were prose until they were written down here, and writing
them down is what turned up the gaps: the endpoint allowlist found nothing
wrong but now makes adding an endpoint a deliberate act, and the credential
check found that a prompt-registry refactor had silently moved prompt text
out of reach of the pattern that was meant to cover it.

**Changing one of these is a breaking change.** It requires a major version
bump and an entry in [CHANGELOG.md](../CHANGELOG.md) under a `Security`
heading. A commitment that can be quietly relaxed is not one.

## The commitments

### C-01 — Nothing is contacted that the operator did not configure

No telemetry, no analytics, no usage reporting, no licence check, no update
ping. The only outbound connections are the model provider you configured,
the Confluence host you supplied, and the public catalog sources fetched by
an `etl-*` command you ran.

**Enforced by**
[`tests/test_no_undeclared_endpoints.py`](../tests/test_no_undeclared_endpoints.py).
Every host-shaped literal in `src/` must appear in a declared allowlist with
a stated reason; the allowlist must contain no telemetry-shaped name; and it
must contain no dead entries, so it cannot drift into permitting more than
the code does.

**Not covered:** what a third-party dependency does. That is the hashed
lockfile, pip-audit and the Dependabot cooldown, and it is a different claim.

### C-02 — No credential ever reaches a model

Not in a system prompt, not in a user prompt, not in the ledger, not in
version history. Credentials are read from environment variables only in
modules declared in the containment test below, each with what it reads, and
are passed as transport credentials.

**Enforced by three tests covering three different halves**, which is worth
separating because each proves something the others do not:

- **Statically**, over the source:
  [`tests/test_credential_containment.py`](../tests/test_credential_containment.py)
  — every module reading the environment is declared with what it reads and
  why; **no module that defines prompt text reads the environment at all**;
  no prompt text names a credential variable. This is pure AST analysis and
  depends on nothing at runtime.
- **At runtime, in the suite itself**:
  [`tests/test_hermetic_credentials.py`](../tests/test_hermetic_credentials.py)
  — no live credential is reachable from a test, by any route.
- **In the audit trail**:
  [`tests/test_llm_ledger.py`](../tests/test_llm_ledger.py) — the ledger
  holds a hash and never content.

**Why the runtime half exists, because the lesson generalizes.** Stripping
credential variables from the environment is not sufficient and this was
measured rather than assumed: LiteLLM calls `load_dotenv()` **at import**,
reads the repository's own `.env` off disk, and writes the keys into
`os.environ` *after* any fixture has run — creating a variable that was
never in the environment to begin with. Every test importing it afterwards
held a live, billable key. The fix disarms dotenv's loader before anything
can import litellm, and the regression test imports litellm on purpose,
since a test that carefully avoided the import would pass while the hole
stayed open.

**Not covered:** a value laundered through several call frames. Nothing
short of taint tracking proves that, which is what CodeQL's
`security-extended` suite is in CI for. What is made impossible is the
version that actually happens — an env read three lines from a prompt
constant, or a dependency repopulating the environment behind a fixture.

### C-03 — Content never travels past its configured ceiling

Licensed catalog content does not reach a third-party model unless you
declare it may. The check runs **before** the call and raises rather than
warns.

**Enforced by** [`tests/test_llm_boundary.py`](../tests/test_llm_boundary.py)
and [`tests/test_side_channels.py`](../tests/test_side_channels.py), the
second of which asserts the guarantee cannot be bypassed by constructing a
provider directly, and covers the embedder and reranker as well as language
models.

### C-04 — The model ledger records metadata and never content

Provider, model, subject, content class, token counts, cost, and a SHA-256
prefix of the prompt. Never the prompt, never the reply. A ledger holding
content would copy licensed material into `output/` — the exact leak it
exists to disprove.

**Enforced by** [`tests/test_llm_ledger.py`](../tests/test_llm_ledger.py).

### C-05 — Licensed content is never written to a redistributable path

BYOC content is read in and never written to a bundled or public location.
`allow_licensed_in_repo` defaults to false and `policyforge check` fails if
licensed content is committed.

**Enforced by** [`tests/test_byoc_boundary.py`](../tests/test_byoc_boundary.py),
checked over the parsed AST across the whole read path rather than as a
substring, so the modules stay free to discuss the rule in their docstrings.

The ETL commands also refuse to write a licensed catalog into the bundled
`data/frameworks/` directory, **even with `--force`**, enforced by
[`tests/test_cli.py`](../tests/test_cli.py): one test writes through five
spellings and working directories and asserts that no file appears inside the
bundled directory, and one confirms a write *beside* it, into
`local_content/`, still succeeds.

**This guarantee was bypassable until 2026-09-16, and the way it failed is
worth knowing.** The guard compared the path's *spelling*, and filesystems do
not resolve paths by spelling. Three destinations wrote a licensed catalog
into the bundled directory with `--force`, exit 0, no refusal:
`Data/Frameworks/…` and `DATA/frameworks/…` on a case-insensitive filesystem
(Windows, and macOS by default), and `frameworks/…` when run from inside
`data/` — which works on **any** platform, Linux included. The guard now asks
the filesystem whether any existing ancestor of the destination *is* a
bundled catalog directory, using `os.path.samefile`, which resolves case,
`..`, symlinks and junctions the way the write does. Three of the five test
cases failed before the fix.

**The first fix was itself incomplete, and closed the same day.** It found
bundled directories only by searching upward from the working directory, and
kept a spelling test as the fallback for everything else — case-sensitively.
So running from *outside* a checkout with an absolute, differently-cased path
(`--out <repo>/Data/Frameworks/…`) wrote a licensed catalog into that
checkout's bundled directory, exit 0, while the lowercase spelling of the same
path was refused. The spelling test now compares path components case-folded,
on the path as given and on its resolved form, so `Data/Frameworks`,
`DATA/FRAMEWORKS` and `data/x/../frameworks` are refused wherever the command
runs; components rather than substrings, so `metadata/frameworks` and
`data/frameworks-old` are still allowed. The tests place the other checkout
beside the working directory and write through an absolute path. One stated
cost: on a case-sensitive filesystem, a `Data/Frameworks` that genuinely is a
different directory is now refused too. A false refusal is recoverable; a
false permission is a redistributed licence.

**Not covered:** the identity check needs the bundled directory to exist on
disk to compare against, and 8.3 short names and hard-linked directories have
not been tested. `samefile` should handle both; it has not been measured, so
it is not claimed.

### C-06 — Model-written code never enters the package unreviewed

`generate-parser` output lands in `output/`, passes a static AST check
against an import allowlist, runs once in a child process under a PEP 578
audit hook refusing sockets, subprocesses and writes, and reaches `src/`
only when a person promotes it.

**Enforced by** [`tests/test_parser_gate.py`](../tests/test_parser_gate.py),
and by a tripwire in [`tests/test_cli.py`](../tests/test_cli.py) that fails if
a test run leaves a generated loader in the real `src/policyforge/ingest/`.
The tripwire exists because the test's own redirect of the promotion
directory was found to break silently when the CLI was split into modules:
with it defeated, a test wrote model-generated code into the package. A
safeguard for this commitment that could fail without a signal needed a
second one that cannot.

**Stated limit:** this is not a sandbox and is not described as one. It
changes the default, not the ceiling.

### C-07 — Nothing reaches a live system without an explicit apply

Dry run is the default on every path that writes to Confluence. A document
without a `confluence:` frontmatter block is never published. A page whose
latest version this tool did not write is reported, not overwritten.

**Enforced by** [`tests/test_content_git.py`](../tests/test_content_git.py)
and [`tests/test_cli.py`](../tests/test_cli.py).

### C-08 — Traceability cannot be dropped silently

A generated statement carries its control attribution. A rewrite that drops
the tag, or weakens a requirement's modal verb, is reported by the local
offline `policyforge check` rather than reaching review as an invisible prose
diff. Both are warnings, so the command exits non-zero on them only under
`--strict`; a repository that wants this as a gate runs it that way.

**Not covered: whether a tag is correct.** The check compares a document's
tags against the synthesis it was written from, which catches a tag that was
dropped between the two and cannot catch one that was wrong in both. Nothing
in the pipeline resolves a tag against the loaded catalogs, so a citation
naming a requirement that does not exist is not detected today.

**Enforced by** [`tests/test_content_tree.py`](../tests/test_content_tree.py)
and [`tests/test_deontic.py`](../tests/test_deontic.py).

## What is deliberately not committed to

Naming these matters as much as the list above, because a commitment list
that quietly omits its exclusions is marketing.

- **That generated documents are correct.** They are *checkable*. See
  [Responsible AI use](responsible-ai-use.md).
- **That prompt injection cannot succeed.** The fence is measured and the
  scanner is a word list. The planner gap is open and documented in
  [residual risk](security-architecture.md#residual-risk).
- **Anything about your model provider's retention or training use.** That
  is your contract with them. See [Subprocessors](subprocessors.md).
- **That the tool is safe to run with privileges its operator should not
  have.** It runs as you, deliberately.

## Verifying these yourself

```bash
python -m pytest tests/test_no_undeclared_endpoints.py \
                 tests/test_credential_containment.py \
                 tests/test_hermetic_credentials.py \
                 tests/test_llm_boundary.py \
                 tests/test_llm_ledger.py \
                 tests/test_side_channels.py \
                 tests/test_byoc_boundary.py \
                 tests/test_parser_gate.py -q
```

`python scripts/check.py` runs these along with every linter and scanner.
[OpenSSF Scorecard](../.github/workflows/scorecard.yml) publishes a
third-party grade of the repository practices behind them.

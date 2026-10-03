---
name: policyforge
description: Operate the PolicyForge CLI in this repo to draft, publish, and version cross-mapped information-security policies/standards/procedures from NIST 800-53/FedRAMP/ARC-AMPE/HITRUST/GovRAMP. Use whenever the user asks to generate, draft, or update a policy/standard/procedure/SOP; synthesize or map compliance controls/frameworks; publish to or pull from Confluence; check what changed in a policy over time (version history/drift); or set up/troubleshoot the policyforge CLI (config.yaml, API key, llm-check, "temperature deprecated" errors). Covers the etl-oscal/etl-hipaa -> map -> synthesize -> generate -> export/import-confluence -> history pipeline, and the ssp (System Security Plan spreadsheet) output path.
---

# PolicyForge operator skill

You are operating the `policyforge` CLI in this repo, not writing new
features for it. For deep architecture/licensing detail this skill doesn't
cover, read the guides at https://zardoz.io, and the module docstrings under
`src/policyforge/`. This skill is the "how do I actually run this" companion.

## Before anything: environment check

1. Activate the venv: `.venv/Scripts/activate` (Windows) or
   `source .venv/bin/activate` (Unix). If `.venv` doesn't exist, run
   `pip install -e ".[dev]"` first (see CONTRIBUTING.md).
1. Confirm `config/config.yaml` exists (copied from `config/config.example.yaml`,
   gitignored, never commit it). If missing, ask the user for their `llm.provider`,
   model, and org details before proceeding — don't invent org name/industry/vendors.
1. Run `policyforge llm-check`. If it fails:
   - `Environment variable X is not set` — the user needs to export their API key
     in their *own* shell session (PowerShell: `$env:VAR = "..."`; cmd:
     `set VAR=...`; bash: `export VAR=...`). You likely can't set it for them if
     your shell tool is a different process than their interactive terminal —
     say so plainly and ask them to run it themselves, then re-check.
   - Any other error — read it; don't guess. AnthropicProvider/VertexProvider
     already retry once without `temperature` if a model rejects it as
     deprecated, so that specific failure mode is already handled.

## The pipeline, in order

Only steps 3-4 are needed per new topic once steps 1-2 have been run once for
the org's framework data.

1. **`policyforge etl-oscal [--out <path>]`** — one-time (or
   refresh-on-demand): fetch NIST's OSCAL release of SP 800-53 Rev 5 into
   `data/frameworks/nist-800-53-r5/controls.json`, baseline-tagged. Skip if
   that file already exists and is current. `policyforge etl-vault --controls-dir <path>` is the alternative for markdown-vault sources —
   only needed if you want its "Cross-Framework Mappings" (FedRAMP) data,
   which the OSCAL catalog doesn't carry.
   HIPAA equivalents, also one-time: **`policyforge etl-hipaa`** then
   **`policyforge etl-hipaa-crosswalk`** (the second attaches NIST's
   HIPAA-to-800-53 mapping; without it `synthesize` won't pull HIPAA in).
   HITRUST, GovRAMP and SOC 2 are bring-your-own, and their loaders are in
   the **licensed-framework plugin, distributed separately** from the public
   PolicyForge. If it isn't installed, each command says so; don't promise
   the user a parse, and see "Try `etl-hitrust`/`etl-govramp` before
   `generate-parser`" below.
   With the plugin, **`policyforge etl-hitrust --export <path>`**
   parses the user's own MyCSF export (CSV/TSV/XLSX/HTML/MHTML) and prints
   what it found. It writes nothing without `--out`, and `--out` refuses
   `data/frameworks/` outright plus any non-gitignored path unless
   `frameworks.allow_licensed_in_repo` is set. Prefer a CSV export: the
   rendered HTML/MHTML of the same report carries far fewer
   authoritative-source mappings.
   GovRAMP is bring-your-own on the same terms:
   **`policyforge etl-govramp --export <path>`** reads the published controls
   matrix workbook (`.xlsx`/`.xlsm`) whole — hand it the file as GovRAMP
   ships it, not an extract, because the controls sheet is found among the
   template's other thirteen by its header captions. Same `--out` gate.
   **Its Core/Ready/Authorized columns are verification tiers, not impact
   levels** — one Moderate matrix holds all three, each tier's set inside the
   next. If the user asks "how many controls do we need", ask which tier
   they are pursuing; answering with the Authorized count when they are
   going for Ready overstates the work several times over.
   Its real value over the bundled 800-53 data is the parameter values
   GovRAMP has already decided — see the `parameters` note below.
1. **`policyforge map --controls <path> [--controls <path> ...] [--out <path>]`**
   — build/refresh the cross-framework crosswalk
   (`data/frameworks/crosswalk.json`). **Pass every framework's controls.json**
   with repeated `--controls`: a cross-framework crosswalk needs them loaded
   together, and passing only the NIST file produces an empty one. The command
   prints which frameworks it mapped — if it says none were found, that's the
   mistake. Re-run after any change to controls.json.
1. **`policyforge coverage --controls <path>... [--baseline low|moderate|high] [--strict] [--json]`**
   — optional but cheap and LLM-free: reports which in-scope controls no
   topic owns (orphaned) and which two topics claim (contested), from
   `config/topics.yaml`. Run it after editing the topic registry, and before
   synthesizing a new topic, to check the new topic isn't claiming controls
   another one already owns. If `config/topics.yaml` is missing, tell the user
   to copy `config/topics.example.yaml` and set the owners to their teams —
   don't invent team names.
1. **`policyforge synthesize --topic-name "<registry topic>" --controls <path>...`**
   — merge/dedupe controls for one topic into synthesized requirement prose.
   Prefer this form: it takes the anchor controls *and* the owning team from
   `config/topics.yaml`, and records the owner in the output so `generate`
   names the real team instead of `[Responsible Team]`. If the user names a
   topic in plain language, match it against the registry first — an unknown
   name errors with the list of available topics. Fall back to
   **`--topic "<name>" --nist-controls <IDs>`** only for a genuine one-off
   that is not in the registry, and say that its documents will use
   `[Responsible Team]` placeholders.
1. **`policyforge generate --tier standard --synthesis <synthesis.md>`**
   first, then, once the Standard exists:
   **`policyforge generate --tier policy --synthesis <synthesis.md> --standard <standard.md>`**
   and/or
   **`policyforge generate --tier procedure --synthesis <synthesis.md> --standard <standard.md>`**.
   Policy and Procedure both require `--standard` — they reference it by
   title (auto-extracted). Never generate Policy/Procedure before Standard
   exists for that topic. Each successful `generate` auto-records into local
   version history (see below) — no extra flag needed.

Separate output path (not part of the Policy/Standard/Procedure chain):

- **`policyforge ssp --controls <path>... [--baseline low|moderate|high] [--system-name "<name>"] [--no-narratives] [--yes]`**
  — build a NIST 800-53 System Security Plan as a LibreOffice-compatible
  `.xlsx` workbook. Makes one LLM request per in-scope control for the
  implementation narratives, so it prompts for confirmation first; pass
  `--no-narratives` to build the workbook without any LLM calls. Needs
  baseline-tagged control data (i.e. `etl-oscal` without `--no-baselines`)
  for `--baseline` to work.

Optional, after generating:

- **`policyforge export-confluence --doc <path> --space <KEY> --title "<title>" --host <url> [--parent-id <id>] [--dry-run]`**
  — publish. Requires `CONFLUENCE_API_TOKEN` (and usually `CONFLUENCE_USERNAME`)
  set in the user's shell. Always offer `--dry-run` first to show the
  converted storage format before actually publishing, unless the user
  clearly just wants it published.

- **`policyforge import-confluence --tier <tier> --name <name> --space <KEY> --title "<title>" --host <url>`**
  — pull the page's *current live* content back and diff it against the last
  locally-recorded version for that tier/name (drift detection — did someone
  hand-edit the published page?). `--name` must match the filename stem
  `generate` used (e.g. `auth-mgmt` for `auth-mgmt.md`).

- **`policyforge edit-confluence --instruction "<what to change>" --space <KEY> --title "<title>" --host <url> [--apply] [--yes] [--allow-macros]`**
  — edit a live page from a plain-language instruction: fetch, plan, rewrite,
  diff, publish. **Always run it without `--apply` first** and show the user
  the plan and the diff; that form changes nothing in Confluence. Only add
  `--apply` after they have seen it and said yes, and prefer letting the
  command's own confirmation prompt run rather than passing `--yes`. If it
  refuses because the page uses unsupported macros, do *not* reach for
  `--allow-macros` on your own — tell the user what would be lost and let
  them decide. If the output warns that framework citations or sections went
  missing, surface that prominently: it means the rewrite dropped
  traceability, and publishing it would be a compliance regression.

- **`policyforge edit-topic --instruction "<what to change>" --topic-name "<topic>" --host <url> [--tiers standard,procedure] [--apply] [--yes]`**
  — apply one instruction across a topic's whole Policy/Standard/Procedure
  set, resolving the pages from the `confluence:` block in
  `config/topics.yaml`. Prefer this over three `edit-confluence` runs when the
  change plausibly touches more than one tier; it plans each page at its own
  altitude and leaves untouched any page whose plan comes back empty. Same
  gates as `edit-confluence` — dry run first, always. If a publish fails
  part-way through, the output names which pages already landed; relay that
  exactly, because the set is then inconsistent.

- **`policyforge zardoz discover --space <KEY> [--host <url>] [--out <path>] [--no-llm]`**
  — propose a `topics.yaml` for a space that has never been catalogued. Writes
  `config/topics.proposed.yaml`, never `topics.yaml`, and every owner comes back
  `[UNASSIGNED]`. **Do not fill those owners in yourself** — ask the user which
  team owns each topic; guessing one into a compliance artifact is the failure
  this deliberately avoids. Check the groupings and anchor controls with the
  user, then have them rename the file and run `coverage` against it.

- **`policyforge zardoz sync`** then **`policyforge zardoz`** — build the local
  document snapshot, then open the conversational shell over it. Two sources,
  either or both: `--content-dir` (or `zardoz.content_dir`) reads a markdown
  tree and **needs no credentials at all**, so prefer it when the user just
  wants to try the shell or is working on local drafts; `--host` (or
  `zardoz.host`) pulls published Confluence pages, with `zardoz.supporting_space`
  optionally adding an extra space as unowned context. Where both are set the
  markdown tree wins, because the file is the source of truth and the page is a
  copy of it. Re-run `sync` after anything changes, then `/reload` inside the
  shell rather than restarting it. Read the report rather than skimming it:
  skipped pages mean a registry title no longer matches a live page, "already
  claimed by topic" means two topics declare one page (a real ownership
  problem — run `coverage`), and `@unresolved-user` counts usually mean an Owner
  field that will read as blank. If sync reports it **refused to write** because
  it resolved nothing, do not reach for `--allow-empty` to make the message go
  away — that clears a working corpus. Find out why nothing resolved. The shell
  is read-only: if the user wants a change made, it drafts an `edit-topic`
  command and you run that.
  Inside the shell it can also run read-only analyses — `/coverage`,
  `/parameters`, `/drift`, `/history`, `/check`, `/frameworks`, `/roles` — and a
  question routes to one automatically, printing `(ran /coverage)` first. Those
  reports are the deterministic command output verbatim, so **quote them rather
  than summarising**; the whole design is that no model paraphrases the numbers.
  If a question routed somewhere wrong, the `(ran /...)` line is how you can
  tell — say so and re-ask rather than working from the wrong report.
  Zardoz cannot publish, edit or export: the Confluence write path is unreachable
  from the package and a test enforces it. For a change, draft `edit-topic`.
  Inside the shell, follow-ups work: "who owns that?" is resolved against the
  previous turns before retrieval, and the rewritten question is printed as
  `(reading that as: ...)`. **If that line shows a question the user didn't
  mean, say so rather than relaying the answer** — `/forget` clears the context.
  If a question's own words find nothing and a model is configured, the shell
  retries with vocabulary the model guesses a document would use, and says so:
  `(nothing matched those words; searched also for: ...)`, with guessed terms
  marked `(guessed)` in the match reasons. Treat a passage found only that way
  as weaker evidence and say which words were guessed.
  Anything not starting with `/` is a question. With no LLM configured it
  returns the matching passages with citations; with one, a grounded prose
  answer built from them (below). **"Nothing in the synced documents appears to bear on that"
  is a real result, not a failure to work around** — it means the corpus does not
  cover the question, which for a control identifier is itself a coverage finding.
  Report it as an answer; do not rephrase the question repeatedly to force a hit.
  If the user believes the document exists, check `/corpus` first — the usual
  cause is a page that never synced, not bad ranking.
  With an LLM configured the passages become prose with a citation per claim.
  **If the output starts with `!!`, the answer failed its own integrity checks**
  — a citation pointing at a passage that was never supplied, no citations at
  all, or a quotation that isn't verbatim in the source. Never relay such an
  answer as fact: read `/sources` and report what the documents actually say.
  Without an LLM the shell returns passages instead of prose; that is a
  supported mode, not a broken one, so don't treat it as a setup problem.

- **`policyforge check [--content-dir <dir>] [--synthesis-dir <dir>] [--strict]`**
  — the repo-backed gate, entirely offline (no credentials, no LLM). Run it
  before any publish and in CI. Errors block: two files claiming one Confluence
  page, a link to a document that isn't there, a `confluence:` block with no
  space. Warnings don't: missing owner or tier. Exits 1 on any error, or on any
  warning with `--strict`.

- **`policyforge publish [--content-dir <dir>] --host <url> [--only <substr>] [--apply]`**
  — push each document to the page its own frontmatter declares. **Plans by
  default; always show the plan before passing `--apply`.** Documents with no
  `confluence:` block are deliberately not published. If it skips a page because
  the live version uses unsupported macros, do *not* reach for `--allow-macros`
  — that flattens them. Tell the user what would be lost.

- **`policyforge pull [--content-dir <dir>] --host <url> [--space K --title T --tier <tier>] [--apply]`**
  — bring live pages down into the tree as markdown, with the frontmatter
  binding written back so the round trip closes. Defaults to every page the
  topic registry declares; `--space`/`--title` pulls one. Same rule on
  `--allow-macros`: a refused page would produce a file that looks correct and
  destroys those macros on its first publish.

- **`policyforge history --tier <tier> --name <name> [--diff latest | --diff N:M]`**
  — list or diff the local version history for one document. Use this
  whenever the user asks "what changed" or "show me the history" for a
  policy/standard/procedure. `--tier confluence` reads the streams the edit
  commands write (`--name` is the page-title slug, e.g.
  `access-control-standard`); those entries print the plan that produced each
  revision, including what the model flagged and what it declined — that is
  the answer to "why did this page change".

- **`policyforge drift --controls <path> [--old <path>] [--content-dir <dir>] [--detail] [--fail-on-change]`**
  — after any ETL re-run, report what a catalog update changed and what it
  reaches. Compares against the git-committed catalog by default, so `etl-oscal`
  then `drift` needs no snapshot. **Never respond to drift by regenerating the
  document set** — that discards every hand edit and review those documents have
  had. Work the blast radius: re-read only the named documents, and re-check any
  recorded parameter decision it flags, because those were decided against
  wording that may have moved. Changes marked editorial need no action.

- **`policyforge parameters --controls <path>... [--baseline <b>] [--topics <path>] [--group] [--init]`**
  — the ledger of organization-defined values. Run it before `synthesize` for a
  new programme: 800-53 leaves ~1,200 values to the organization, and without a
  decision recorded the model picks each one inside the prose, inconsistently.
  **Always scope it** (`--baseline`, `--topics`) — the whole catalog is a
  thousand parameters and is not a to-do list. `--group` orders by leverage.
  `--init` scaffolds the file and preserves existing decisions.
  **Do not fill in values yourself.** A frequency is a commitment the
  organization has to defend to an assessor; ask the user, and record their
  reasoning in `rationale`/`source`. A parameter left blank stays as
  `[Assignment: ...]` in the output, which is the correct outcome for an
  undecided value — not a bug to paper over.
  The one exception is a value a *framework* has already decided. A GovRAMP
  matrix ingested with `etl-govramp` carries a few hundred of them, and they
  reach synthesis automatically — that is the framework's answer with a
  citation behind it, not a guess. Still say so when it happens: the user is
  committing to GovRAMP's cadence, and they should know that is where it came
  from.

- **`policyforge roles`** — list the tool and team roles config can assign.
  Run it before editing `org.vendors`/`org.teams`: the keys are fixed, and a
  typo'd one is reported and ignored rather than guessed at. Role-keyed values
  are substituted deterministically after generation, so `[Identity Provider]`
  becomes the configured name in every document or in none.

- **`policyforge frameworks`** — list the catalogs on disk and their licence
  position. Exits 1 if licensed content is committed to a repository that has
  not declared `frameworks.allow_licensed_in_repo: true`.

- **`policyforge boundary [--path <file>...]`** — what may be sent to the
  configured model. Prints how the provider was classified (local /
  self-hosted / third-party) and the content-class ceiling table; with
  `--path`, classifies specific files and exits 1 on a refusal. Run it when a
  command refuses, or before a run against a licensed catalog, rather than
  guessing why.

- **`policyforge model-log [--by model|subject|site|provider|content_class]`**
  — what was sent to which model, when, and at what cost. Every call is
  recorded (provider, the model that *answered*, the document or control,
  tokens, cost, a prompt hash — never the prompt or the reply). Filter with
  `--subject`, `--model`, `--since`. This is how you answer "which documents
  did that model touch". `$0.0000` and `unpriced` mean different things: a
  local model is free, an unpriced provider is unknown.

## Guardrails — do not skip these

- **Never commit `output/` or `config/config.yaml`.** Both are gitignored on
  purpose — generated policies are org-specific content, not engine code. If
  you're asked to commit changes, `git status` first and make sure nothing
  under those paths is staged.

- **Try `etl-hitrust`/`etl-govramp` before `generate-parser`.** With the
  licensed-framework plugin installed (it is distributed separately), both are
  implemented and neither makes an LLM call. They handle the exports seen so
  far. Without the plugin, each says so and points to `generate-parser`. Reach
  for codegen when the plugin isn't installed, or when the ETL command fails —
  it names the fields or columns it could not find.

- **Licensed content may only reach a local model, and this is enforced.**
  A catalog whose `framework.yaml` says `licence: licensed`, one with no
  manifest at all, or anything under `local_content/` is licensed content.
  `synthesize` checks every `--controls` path before reading any of them, and
  `generate-parser` checks its `--sample`; against a hosted provider
  (Anthropic, Bedrock, Vertex, LiteLLM) both refuse and `--yes` does not get
  past it. **This bites the ordinary case**: the "pass every framework's
  controls.json" advice above sends a HITRUST or GovRAMP catalog to the
  model, so on a hosted provider it refuses.

  When it does, do not work around it. Show the user the refusal — it names
  the content class, the provider class and the reason — and offer the two
  real options: point `llm:` at a local model (Ollama, `base_url: http://localhost:11434/v1`) for that run, or synthesize from the
  public-domain catalogs alone. A third exists and is theirs to choose, not
  yours: if their organisation has a data agreement that covers sending
  licensed content to that hosted provider, they declare the provider under
  `llm.licensed_agreement` (provider; `base_url` or `vendor` where the
  provider is `openai-compat`, `local` or `litellm`; optionally model and
  reference).
  Do not set that on their behalf, for the same reason you do not set
  `allow_licensed_in_repo`. `llm.classification` is not a route for licensed
  content: it states where a provider is, and a declared `local` that is not
  this machine is refused. `policyforge boundary` explains any refusal in
  full.

- **`generate-parser` sends real file content to the LLM API.** The boundary
  check above already refuses a licensed `--sample` on a hosted provider.
  What it cannot decide is whether the user's licence permits this use at
  all, even locally — so still confirm that with them rather than proceeding
  because they asked. A synthetic/dummy sample needs no such confirmation.
  Trimming the sample to a dozen rows with the header intact is usually
  enough and sends far less.

- **`generate-parser` output is a candidate, not a module.** It lands in
  `output/parsers/`, is refused before it runs if it imports anything off
  the allowlist or tries to write, and what passes is trial-run once under
  an audit hook with the record count reported. **Do not pass `--promote`
  on the user's behalf** unless they have read the candidate: the checks are
  not a sandbox, and promotion is the step that puts model-written code on
  the package's import path. When a candidate is refused, show the lines the
  refusal names — do not edit the candidate to get past them.

- **BYOC frameworks (HITRUST, GovRAMP) never go in `data/frameworks/` or get
  committed *to this repository*.** They live in `local_content/` (gitignored).
  **In a user's own repository the answer is often different**: their MyCSF
  licence commonly permits their private repo to hold the export, and they
  declare that with `frameworks.allow_licensed_in_repo: true`. Do not set that
  flag on their behalf — it is a statement about their licence, and only they
  can make it. If `check` reports a breach, explain what it means and let them
  decide. The licensing notes are at https://zardoz.io.

- **Org context (`config.yaml`'s `org:` block) drives placeholders.** If
  `vendors` is empty, generated docs use `[Square-Bracket Vendor]`
  placeholders rather than inventing product names — that's correct
  behavior, not a bug to fix.

- **Local version history (`output/.history/`) is a convenience, not the
  system of record.** Don't present it as an authoritative changelog if the
  user's actual system of record is Confluence, git or a GRC platform — it's a
  supplementary local trail, per `history/version_store.py`'s docstring.

## Typical end-to-end request

User: "Draft a password policy and standard for us, then publish the
standard to Confluence."

1. Check env (above).
1. `synthesize --topic-name "Authentication & Credential Management" --controls data/frameworks/*/controls.json`
1. `generate --tier standard --synthesis output/synthesis/password-credential-management.md`
1. `generate --tier policy --synthesis output/synthesis/password-credential-management.md --standard output/standards/password-credential-management.md`
1. Show the user both drafts before publishing anything.
1. On confirmation: `export-confluence --doc output/standards/password-credential-management.md --space <ask if unknown> --title "<from doc title>" --host <ask if unknown>`

Don't skip step 5 — these are compliance documents; publish only after the
user has actually looked at the draft.

# Where PolicyForge sits

For anyone comparing this with a compliance platform, and for anyone who has
to explain the difference in a meeting.

Every claim about PolicyForge here points at a file, a test or a measured
run, the same rule the rest of [docs/](README.md) follows. Every claim about
another product is that product's own description of itself, quoted with its
source and the date it was read, because the honest version of this page is
the one a competitor could read without objecting.

## The one-page version

**PolicyForge turns overlapping control catalogs — HITRUST CSF, the HIPAA
Security Rule, NIST 800-53, FedRAMP, ARC-AMPE, GovRAMP — into policies,
standards and procedures an engineer can execute, organized so every topic
has one accountable owner.** It uses a language model you bring the key for,
grounded strictly in the control text you supply rather than the model's
recollection of what a framework says
([zardoz.io](https://zardoz.io)).

**One topic, one team.** Catalogs are organized for the auditor, by control
family. Engineering organizations are organized around the people doing the
work. Most compliance documentation keeps the auditor's shape and hands it to
engineers. The synthesis step transposes: one document per topic, each with a
single accountable team, and `policyforge coverage` names the in-scope
controls no topic owns
([zardoz.io](https://zardoz.io)).

**What is built.** The pipeline runs end to end — `map` → `synthesize` →
`generate` → publish — over four bundled public-domain catalogs, each
re-fetchable from its source. Bring-your-own loaders for HITRUST CSF, GovRAMP
and SOC 2 are in the licensed-framework plugin, distributed separately
([zardoz.io](https://zardoz.io)).

**What it costs.** Twenty topics at four documents each: **$0.28 and 103
minutes** on `glm-5.3-flash`; **about $12** extrapolated on
`claude-sonnet-5`, from five topics measured rather than twenty, so read it
as an order of magnitude. No eval graded those documents — the figures say
what a run costs, not whether the output is good
([MEASUREMENTS.md](../MEASUREMENTS.md), epoch 21).

**What it refuses to claim.** Nothing it produces is evidence of compliance.
It drafts documents a competent person must read, correct and own. The
[system card](system-card.md) is the one-page account for a vendor review;
[commitments.md](commitments.md) lists eight behaviours with the test that
fails when each stops being true.

## Who it is for

From [Responsible AI use](responsible-ai-use.md#feasibility-who-this-puts-the-work-within-reach-of):

> Running HITRUST and a NIST-based program simultaneously is, today, work
> that requires either a dedicated GRC team or a consultancy engagement. The
> organizations that most need good security documentation — small clinics,
> regional providers, early-stage health technology companies, the suppliers
> sitting in everyone else's risk register — are precisely the ones that can
> afford neither.

So the buyer is a security or compliance lead at an organization carrying
HIPAA because it is law and HITRUST because a contract demands it, with a
security function of one to five people, no dedicated GRC team, and a policy
set that is either boilerplate or absent. The competing option is rarely
another tool. It is a template pack, a consultancy engagement, or nothing.

## Against a compliance automation platform

A category of compliance and GRC platforms sits next to this one. As their
own marketing described them in September 2026, they offer compliance and
risk management that is automated and continuously monitored, evidence
collected automatically across dozens of frameworks, controls monitored
continuously, control mapping automated, and real-time risk data turned
into insight. Several had by then begun advertising an agent that drafts
policies and completes questionnaires.

Named comparisons are deliberately absent. The distinction below is about
what a category of tool is for, and it does not get sharper by naming
vendors; it only invites their attention and dates the moment one of them
changes their page.

**These platforms answer "is this control working, and where is the
evidence?" PolicyForge answers "what does this control require of us, who
does it, and what does the document say?"** A platform tracks the policy as
an artifact with a review date and an owner. Something still has to write
the artifact, and for most of these buyers that something is a template pack.
PolicyForge is upstream of that: it is where the document comes from.

The overlap is real and worth stating plainly: **some of these platforms
now advertise agents that draft policies**, and several ship policy
templates or generators. What this project offers against that is not "we also draft" but
how the drafting is constrained:

- **Grounded in the catalog text you supply**, never the model's
  recollection of a framework
  ([LLM09](owasp-llm-top-10.md#llm09--misinformation)).
- **Every statement carries its control attribution**, and an offline check
  reports a rewrite that drops the tag before publication — as a warning, or
  a failure under `--strict`
  ([C-08](commitments.md#c-08--traceability-cannot-be-dropped-silently)).
- **Values nobody decided stay visible.** An unfilled parameter stays
  `[Assignment: organization-defined frequency]` rather than becoming a
  plausible number, and the prompts forbid inventing a frequency or deadline
  the input does not state (MEASUREMENTS.md epoch 18).
- **Where your content may go is enforced before each call**, not described:
  licensed catalog content reaches only a local model unless you say
  otherwise, and the check raises rather than warns
  ([C-03](commitments.md#c-03--content-never-travels-past-its-configured-ceiling)).
- **The prompts are measured, including the runs that got worse**
  ([MEASUREMENTS.md](../MEASUREMENTS.md)).

### Feeding a platform

There is **no integration with any of these products**, and none is planned
in the repository today. `policyforge export-confluence` and `publish` write
to Confluence; everything else is markdown on disk
([`export/`](../src/policyforge/export/)). A platform that tracks a policy by
URL or by upload takes those documents the way it takes any others: you
attach the file or link the page. Anyone who needs more than that should
treat it as work to do, not a feature to expect.

## Against HITRUST MyCSF

MyCSF describes itself as "a SaaS platform that manages the HITRUST
assessment and certification lifecycle, enabling collaboration, evidence
collection, and results sharing"
([hitrustalliance.net](https://hitrustalliance.net/mycsf), read 2026-09-17).

**Complementary, and in one direction only.** PolicyForge reads a MyCSF
export — CSV, workbook, HTML or MHTML — through `etl-hitrust`, and uses those
requirements alongside NIST, FedRAMP and ARC-AMPE when synthesizing a topic
(the parser is part of the licensed-framework plugin, distributed separately).
It does not conduct an assessment, submit one, score one, or talk to MyCSF at
all. The export is a file you already have; nothing here replaces the
assessment or the assessor.

It is also careful with that file. HITRUST CSF is licensed content: it is
never committed to this repository, `allow_licensed_in_repo` defaults to
false, and licensed content may reach only a local model unless you widen
that ceiling yourself
([C-05](commitments.md#c-05--licensed-content-is-never-written-to-a-redistributable-path),
licensing notes at [zardoz.io](https://zardoz.io)).

## What it is not

- **Not a monitoring platform.** Nothing here watches a control, collects
  evidence, or tells you whether a control is operating.
- **Not evidence.** Documents are not evidence of operation; tickets, logs
  and signed reviews are
  ([Responsible AI use](responsible-ai-use.md#uses-this-tool-should-be-refused-for)).
- **Not an assessor**, and not a certification path.
- **Not a service.** It is a command-line tool you run, with no account, no
  telemetry and no data reaching the maintainers
  ([C-01](commitments.md#c-01--nothing-is-contacted-that-the-operator-did-not-configure)).
- **Not a substitute for review.** Its accuracy controls make a document
  *checkable*, not correct
  ([Responsible AI use](responsible-ai-use.md#the-limits-of-the-claim)).

## If you are evaluating it

1. [System card](system-card.md) — one page: intended use, prohibited uses,
   limitations, data handling.
1. [Commitments](commitments.md) — eight promises, each with its test, and
   the command to run them yourself.
1. [Security architecture](security-architecture.md#residual-risk) — the
   residual risks, stated rather than buried.
1. [Subprocessors](subprocessors.md) — every party that can receive your
   content, all of them ones you configure.
1. [MEASUREMENTS.md](../MEASUREMENTS.md) — what was measured, including the
   changes that made things worse and were reverted.

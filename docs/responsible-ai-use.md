# Responsible AI use

The case that using a language model to draft compliance documentation is a
legitimate and beneficial application of the technology, the conditions that
claim depends on, and the uses this tool should be refused for.

## Contents

- [The claim](#the-claim)
- [Why the problem is a language problem](#why-the-problem-is-a-language-problem)
- [Accuracy: how a model makes this more correct, not less](#accuracy-how-a-model-makes-this-more-correct-not-less)
- [Feasibility: who this puts the work within reach of](#feasibility-who-this-puts-the-work-within-reach-of)
- [What the model is not allowed to decide](#what-the-model-is-not-allowed-to-decide)
- [Human accountability](#human-accountability)
- [What must never go in](#what-must-never-go-in)
- [Uses this tool should be refused for](#uses-this-tool-should-be-refused-for)
- [Disclosure to assessors](#disclosure-to-assessors)
- [Bias, fairness and labour](#bias-fairness-and-labour)
- [Environmental and cost honesty](#environmental-and-cost-honesty)
- [The limits of the claim](#the-limits-of-the-claim)

## The claim

**Using a language model here increases the accuracy and the feasibility of
implementing a security compliance program, and the alternative it replaces
is not a better process — it is usually no process, or a worse one.**

That is a strong claim, and it is worth being precise about what it rests on,
because "AI makes it faster" is not an ethical argument and neither is "the
documents look good."

The argument has three parts:

1. The gap between a control catalog and an executable procedure is a
   **language** problem before it is a data problem, and it is the kind of
   language problem models are genuinely good at.
1. The tool is built so that a model's output is **checkable against a
   source**, which converts a fluency risk into a verification task.
1. The realistic alternative for most organizations is **copied boilerplate
   nobody owns**, which is worse on every axis including honesty.

## Why the problem is a language problem

A healthcare organization rarely gets to pick one framework. It carries the
HIPAA Security Rule because it is law, HITRUST CSF because a contract demands
certification, and often a NIST-based program on top. These catalogs cover
largely the same ground in different words, at different granularity, with
different prescribed values.

The obvious fix is a crosswalk, and both NIST and HITRUST publish one. In
practice the published mappings are necessary and nowhere near sufficient,
for reasons that are structural rather than fixable by a better spreadsheet:

- **They are bare ID pairs.** NIST's OLIR format has fields for relationship
  type and rationale, and in the published data **those fields are empty**.
  What you get is "these two identifiers are related somehow."
- **The fan-out is unusable at the row level.** One HIPAA citation —
  § 164.316(b)(2)(iii), on updating documentation — maps to 21 separate NIST
  controls. An engineer handed that row has a matrix, not a task.
- **Granularity does not line up.** NIST AC-2 is twelve lettered parts. A
  mapping points at *AC-2*, not at which part it corresponds to.
- **Prescribed values are missing on one side and specified on the other.**
  800-53 Rev 5 carries roughly 1,600 organization-defined parameters. HITRUST
  frequently states a concrete value and varies it by implementation level.
  Somebody still has to decide the number, once, and defend it to both
  assessors.
- **Control text is declarative; procedures are imperative.** "Review
  accounts for compliance with account management requirements \[Assignment:
  frequency\]" and "every quarter, the IAM team exports the Okta user list,
  reconciles it against Workday active employees, and opens a ticket per
  exception" are different genres of writing. **Nothing in a crosswalk
  performs that translation.**
- **No framework tells you who does the work.** AC-2 alone touches identity
  engineering, HR onboarding, and individual application owners.

Collapsing requirements that say the same thing in different vocabulary,
keeping genuinely conflicting ones apart, and rewriting declarative control
language as ordered steps is **language work that a join cannot do**. That is
the specific task delegated to a model here, and it is a task where a model's
actual competence — paraphrase, alignment, register shift — is the competence
the job needs.

This matters ethically because the honest test of an AI application is not
"did it produce output" but **"was the model asked to do something it is
actually good at, or something it merely appears good at?"** Asking a model
to *recall* what HITRUST 01.c says is the second kind of task. Asking it to
*reconcile two passages you supplied* is the first. This tool is built around
that distinction.

## Accuracy: how a model makes this more correct, not less

The intuition runs the other way — models fabricate, so a model near
compliance documentation should make it less accurate. That intuition is
right about the naive use and wrong about this one, for four reasons.

**1. It is grounded in supplied text, not recollection.** The model is never
asked what a framework says. It is handed the control text and asked to
reconcile it. A model's memory of a catalog is exactly the unreliable thing;
its ability to work over text in front of it is not.

**2. Every statement carries its source, and the tag is enforced.** Generated
documents tag statements back to the controls they came from. `policyforge check` **reports a rewrite that dropped a traceability tag**, and fails the
build under `--strict` — a change no reviewer reading a prose diff would ever
catch, and one that silently destroys the only thing making the document
auditable. Two limits worth stating: it is a warning unless you ask for
`--strict`, and it compares the document against the synthesis it came from,
so a tag that is wrong in both matches itself and passes.

**3. Claims are verified rather than requested.** Citation markers are parsed
and checked against the passages actually supplied; fabricated ones are
reported. On providers that support it, cited spans come back extracted by
the API from the document, so they are **verbatim by construction rather than
by verification**. And weakened requirement language ("must" quietly becoming
"should") is detected mechanically.

What is checked only if you ask for it: whether the cited passage actually
**supports** the sentence, as opposed to merely existing. An entailment
checker for exactly that question lives in
[`entail/`](../src/policyforge/entail/), and setting `entail.answering: true`
runs it over the answers `zardoz` gives, warning on a claim its own citation
does not carry. It is **off by default**, it covers the answering path only —
not generated documents — and its effectiveness has not yet been measured, so
this document claims no rate for it. Everywhere it is off, a statement can
carry a real citation to a real passage that does not bear it, and only a
human reader will notice. That remains the largest open gap in the accuracy
argument, and it is named here rather than left for a reader to find.

It is also narrower than it sounds where it does run: one extra model call
per cited sentence, it refuses to run on a provider that cannot be held to a
schema, and it requires you to name a **second** model, because a model
checking its own work shares every blind spot it had while writing it.

**4. The failure modes are measured, not assumed.** The prompts are graded
against eval suites, and the numbers, including the regressions and the two
occasions the grader itself turned out to be wrong, are published in
[MEASUREMENTS.md](../MEASUREMENTS.md). A tool that publishes the runs where
it failed is making a different kind of claim than one that publishes a
feature list.

**Compare the realistic alternative.** Most organizations in this position do
one of three things: buy a template pack and change the company name; copy a
previous employer's documents; or write nothing and answer the assessor from
memory. All three produce documents with **no traceability to any control**,
no stated owner, and no way to tell whether a requirement was met or merely
mentioned. Against that baseline, a grounded, tagged, checked draft is not a
degradation in accuracy. It is the first version of these documents that can
be checked at all.

## Feasibility: who this puts the work within reach of

The ethical weight of this project is here rather than in efficiency.

Running HITRUST and a NIST-based program simultaneously is, today, work that
requires either a dedicated GRC team or a consultancy engagement. The
organizations that most need good security documentation — small clinics,
regional providers, early-stage health technology companies, the suppliers
sitting in everyone else's risk register — are precisely the ones that can
afford neither. What they do instead is buy boilerplate, and boilerplate is
how you get a policy set that describes a company nobody works at.

**Lowering the cost of doing this properly is not a convenience; it changes
who can do it properly at all.** A tool that lets a two-person security
function produce traceable, owned, framework-mapped documentation is doing
something with a real welfare consequence, because the people protected by a
functioning security program at a small provider are patients who had no say
in which vendor held their records.

That is the affirmative case. It is also the reason the honesty constraints
below are strict rather than decorative: **a tool that made bad compliance
documentation cheap would make things actively worse**, by letting more
organizations believe they have a program when they have a document set.

## What the model is not allowed to decide

A deliberate boundary, and the clearest expression of how this tool treats
the technology.

| Decision                                       | Who makes it                                                                          |
| ---------------------------------------------- | ------------------------------------------------------------------------------------- |
| What a control requires                        | The catalog. The model reconciles text; it does not supply requirements.              |
| The value of an organization-defined parameter | **A person**, recorded once in the parameters ledger and defensible to both assessors |
| Which team owns a topic                        | **A person**, in the topic registry                                                   |
| Whether a control is in scope                  | **A person**, via scoping and coverage review                                         |
| Whether a document is correct                  | **A person**, reviewing it                                                            |
| Whether the document is published              | **A person**, applying a plan they read                                               |
| Whether the organization is compliant          | **An assessor**, and never this tool                                                  |

The model drafts prose from text it was given. Every judgement that carries
legal, contractual or safety weight is held outside it — not because a model
could not produce an answer, but because **a decision nobody made is a
decision nobody can defend**, and defending these decisions is the entire
purpose of the documents.

## Human accountability

The design commitment that makes the rest coherent: **one topic, one
accountable team.**

Compliance catalogs are organized for the person auditing the work. Engineering
organizations are organized around the people doing it. Most compliance
documentation fails because it keeps the auditor's shape and hands it to
engineers. This tool transposes: one document per topic, each with a single
accountable owner, and `policyforge coverage` reports in-scope controls no
topic owns so a gap is visible rather than silent.

The test is **ownership, not step count**. A topic can involve several teams'
work — user lifecycle touches HR, IAM, IT support and app owners — and still
be one topic, because one team can own the process end to end. What breaks a
topic is cross-team *accountability*, where two owners each assume the other
has it.

**This matters for AI ethics specifically.** The characteristic failure of
generated documentation is diffusion of responsibility: the document exists,
so somebody must have decided it was right, and no one person did. Requiring
a named owner per document, before generation rather than after, is the
structural answer to that. **Generate nothing you have not decided who owns.**

## What must never go in

The tool classifies content by **where a file came from**, not by what is
inside it. `organization-internal` is a statement of provenance. It cannot
know that somebody pasted something into a context file.

**Never place in company context, topic registries, prompts, or any file this
tool reads:**

- **Protected health information, or any patient-identifiable data.** There
  is no reason a policy document needs it, and putting it there creates a
  business-associate problem with your model provider that no control in this
  repository addresses.
- **Credentials, keys, tokens, or connection strings.**
- **Customer or employee personal data**, including names in incident
  examples. Describe roles, not people.
- **Live vulnerability detail** — unremediated findings, specific
  exploitable misconfigurations. Policy describes the control, not the hole.
- **Anything covered by an NDA whose terms you have not checked against your
  model provider's terms.**

If your program will process anything of this kind, use the boundary
configuration to hold `organization-internal` content to a `self-hosted` or
`local` model, and verify with `policyforge boundary` before you run. That is
what the ceiling mechanism is for.

## Uses this tool should be refused for

Stated plainly, because a compliance tool that does not name its misuses is
not being honest about what it makes easy:

- **Producing documentation to imply a control exists when it does not.** A
  written policy is not an implemented control. A tool that makes the
  document cheap makes this misuse cheap too, and it is fraud when presented
  to an assessor or a customer.
- **Generating evidence.** Documents are not evidence of operation. Tickets,
  logs, signed reviews and attestations are. Nothing this tool produces
  should be offered as evidence that a procedure *ran*.
- **Answering a security questionnaire or an assessor's question with output
  nobody reviewed.** An answer you did not read is a statement you cannot
  stand behind.
- **Back-dating.** Version history records when this tool produced something.
  Presenting a document generated today as having governed behaviour last
  year is misrepresentation, and the history file that makes this checkable
  is on your own disk.
- **Redistributing licensed catalog content.** HITRUST CSF and GovRAMP
  material is licensed for your use. The repository defaults and the
  `licensed` content class exist to stop this happening by accident; they
  cannot stop it happening on purpose.

## Disclosure to assessors

**Recommendation: disclose, in writing, before you are asked.**

There is no regulation today requiring an organization to state that its
policy documentation was drafted with model assistance. There is, however, a
straightforward reason to do it: an assessor who discovers it independently
has to re-evaluate everything you told them, and an assessor who was told up
front has a fact rather than a discovery.

A practical disclosure names four things:

1. That drafting was model-assisted, and which models. The ledger and the
   version history's provenance metadata give you this per document.
1. That drafting was **grounded in supplied catalog text** rather than model
   recall, and that statements carry traceability tags back to controls.
1. **Who reviewed and owns each document**, and that no parameter value,
   scoping decision or ownership assignment was made by a model.
1. What went to which processor. `policyforge model-log` and
   `policyforge boundary` answer this.

That disclosure is stronger than silence, and it is stronger than most of
what it is competing with — because the template pack down the road was also
not written by the organization using it, and nobody can say by whom.

## Bias, fairness and labour

Three smaller points that belong in an honest account.

**Bias.** The content risk here is not demographic bias in the usual sense —
these are control documents, not decisions about people. It is **register and
origin bias**: models trained largely on public corpora reproduce the
security conventions of large, well-resourced, US-centric enterprises. A
generated procedure may assume an Okta, a Workday, a 24/7 rotation and a
dedicated security team. For a small provider, that is not a neutral default;
it is a document describing somebody else's company, and it is the failure
mode this tool exists to prevent. **Read generated procedures specifically
for steps your organization has no one to perform**, and treat the topic
registry as the correction mechanism.

**Labour.** This tool does not eliminate a compliance role; it changes what
that role spends its time on — from transcription and reconciliation to
judgement, scoping and review. That is a genuine improvement in the work, and
it is worth stating plainly that it is also a reduction in the volume of
work, which for a consultancy is a business model and for an in-house team is
usually relief.

**Access.** The engine is Apache-2.0 and the public catalogs it ships are
government works. The licensed frameworks are not, and that boundary is
enforced rather than described. The licensing notes are in the guides at
[zardoz.io](https://zardoz.io).

## Environmental and cost honesty

Model inference has an energy cost, and this tool makes many small calls
rather than a few large ones.

**Raising output budgets lowered total spend**, which was measured rather
than assumed: a budget too tight to finish still bills twice. A reply cut off
at its budget is retried once at twice that budget, capped at 65,536 tokens
(twice the 32,768 a Standard or Procedure starts with), and a reply still cut
off after that is refused rather than written (`llm/effort.py`). Separately,
the Anthropic and LiteLLM providers re-send an *empty* cut-off reply at eight
times the budget, which is the 8x retry this paragraph used to describe as the
only one. **No request asks a model for more than the output ceiling it
publishes**, where one is published (`llm/ceiling.py`): the retry and the 8x
re-send are clamped to it, and a re-send the clamp would leave no larger than
the first attempt is not sent at all, because it would bill the same empty
reply again. Every re-send is announced before it goes, with its worst case
priced at the clamped budget. That price is an estimate, and the notice says
where it came from: the price table bundled with the installed LiteLLM (named
with its version), or PolicyForge's override table where that table is wrong
or silent (named with its source and date, `llm/litellm_map.py`). The table
is pinned, never downloaded at startup, so the estimate changes only when
LiteLLM is upgraded (#79). What a run actually cost is the provider's own
reported figure where it gives one.

**Prompt caching and batching are narrower than they sound.** A cacheable
prefix is marked only by `ssp` and `synthesize`, and only does anything on a
provider that implements it — the Anthropic and Vertex providers, not
LiteLLM. Batching is the Batch API, used by `ssp --batch`, Anthropic only.
Neither has a measured saving to report: epoch 21 recorded zero cached input
tokens, because both runs went through LiteLLM.

A full sweep of the eval suites costs cents, not dollars. That is not an
environmental claim of any significance; it is just the honest scale.

## The limits of the claim

The argument above holds only while its conditions do. It fails if:

- **Documents are published without a human reading them.** Every accuracy
  control in this repository produces a *checkable* document, not a correct
  one. Checkability is worth nothing unchecked.
- **Nobody owns the documents.** Then the tool has produced the same
  ownerless artifact as a template pack, faster.
- **The output is treated as evidence** rather than as documentation.
- **The model is chosen on price alone.** On the edit path this is
  measurably a security decision: some models carry out an instruction
  planted in a wiki page and some do not. See
  [OWASP LLM01](owasp-llm-top-10.md#llm01--prompt-injection).
- **Sensitive data is fed in** on the assumption that classification protects
  it. Classification is about provenance, not contents.

**PolicyForge is a drafting tool. It does not assess, certify, or attest to
anything. It produces documents a competent person must read, correct, and
put their name to — and the whole design exists to make that reading
possible, not to replace it.**

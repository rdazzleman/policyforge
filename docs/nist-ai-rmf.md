# NIST AI Risk Management Framework — alignment

How PolicyForge maps to the four functions of the **NIST AI Risk Management
Framework (AI 100-1)** and the seven trustworthiness characteristics it
defines.

## Scope, and what this document is for

The AI RMF governs an **organization's** management of AI risk. PolicyForge
is a tool inside somebody's organization, so it cannot be "AI RMF compliant"
any more than a database can be SOC 2 compliant. There is no certification
here and none is claimed.

What this document does is narrower and more useful: **it tells an adopter
which parts of their own AI RMF work this tool has already done, and which
parts it hands back to them.** If you are standing up an AI inventory, an AI
use-case review, or an ISO/IEC 42001 management system, these are the rows
you can fill in from the tool's own artifacts rather than from a
questionnaire.

Mapped at the function and category level. Exact subcategory identifiers
move between Playbook revisions, so confirm those against whichever revision
your program is using rather than against this table.

## The four functions

### GOVERN — policies, accountability, culture, third parties

| What the framework asks                               | What this tool provides                                                                                                                                             | What you still own                                                   |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Policies and processes for AI use are documented      | [Responsible AI use](responsible-ai-use.md) states intended use, prohibited use, and the decisions a model is not permitted to make                                 | Adopting it as *your* policy, and deciding whether you agree         |
| Accountability is assigned and unambiguous            | **One topic, one accountable team** is the tool's organizing principle, and `policyforge coverage` reports in-scope controls no topic owns                          | Naming the owners in your topic registry, before generation          |
| Risks from third-party software and data are governed | [Subprocessors](subprocessors.md) enumerates every party that can receive content; [supply chain](security-architecture.md#supply-chain) covers the dependency path | Your contract with the model provider — retention, training use, BAA |
| Legal and regulatory requirements are understood      | [EU AI Act positioning](#eu-ai-act-positioning) below; licensing enforced per framework                                                                             | Confirming the positioning with your own counsel                     |

**Strongest here:** accountability. The tool structurally refuses to produce
a document with no named owner as a *normal* output, which is the single
most common governance failure in generated documentation.

### MAP — context, categorization, and what could go wrong

| What the framework asks                                     | What this tool provides                                                                                                                        | What you still own                                           |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| The AI system's context and purpose are established         | [System card](system-card.md) — intended use, out-of-scope use, users, limitations, on one page                                                | Recording it in your AI inventory                            |
| Risks and benefits are mapped, including from third parties | [OWASP LLM Top 10 mapping](owasp-llm-top-10.md), with residual risk named per entry                                                            | Deciding whether the residual risks are acceptable to you    |
| The system is categorized by impact                         | This tool drafts documentation; it makes no automated decision about any person                                                                | Your own categorization — which will differ if you extend it |
| Untrusted inputs are identified                             | An explicit [untrusted-input inventory](security-architecture.md#the-untrusted-input-inventory) naming four input classes and where each lands | Controlling write access to the wiki corpus you point it at  |

### MEASURE — evaluation, metrics, and tracking over time

This is the function where the project is furthest ahead of typical practice,
and it is worth being specific about why rather than just claiming it.

- **[MEASUREMENTS.md](../MEASUREMENTS.md) is a versioned evaluation record**,
  organized into configuration epochs, with the model, the repeat count, the
  cost, and the pass rate per case.
- **It publishes failures and reversions**, not just improvements: a shorter
  trailing reminder that made results worse and was reverted; prompt wording
  that moved a score the wrong way and was reverted.
- **It documents two occasions the grader itself was wrong** and was
  corrected — the measurement discipline turned on its own instruments.
- **Security properties are measured, not asserted.** Prompt-injection
  resistance is a pass rate per model per case, which is what makes "model
  choice is a security control on the edit path" a finding rather than an
  opinion.
- **Prompt fingerprinting** records which prompt text produced which numbers,
  so a run cannot silently be graded against code that has since changed.
- **Gaps are recorded as gaps.** The native-citation path is written up as
  *unmeasured, and here is why*, rather than being quietly counted.

**What you still own:** measuring the outputs *for your organization*.
Nothing here tells you whether a generated standard is right for your
environment. That is a human review, and it is the control the whole design
depends on.

### MANAGE — prioritization, response, and monitoring

| What the framework asks           | What this tool provides                                                                                                                               | What you still own                                       |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| Risks are prioritized and treated | [Residual risk](security-architecture.md#residual-risk) is ranked and each item names who owns it                                                     | Accepting, mitigating or declining each one              |
| Third-party risk is managed       | Content ceilings enforced before each call, with refusal rather than warning                                                                          | Declaring your own tighter ceilings                      |
| Changes are monitored over time   | Content-hashed version history; `policyforge drift` for catalog changes; `wiki-drift` for live-page changes; the model ledger for what was sent where | Retaining these somewhere durable — they are local files |
| Incidents can be investigated     | `model-log` answers "which documents did this model touch"; version history answers "what did this say before"                                        | An incident process that uses them                       |

**Weakest here, honestly:** there is no built-in incident playbook for a
poisoned corpus, and the ledger is a local file with no tamper-evidence
beyond content hashing. Both are named in residual risk.

## The seven trustworthiness characteristics

| Characteristic                      | Standing                  | Evidence                                                                                                                                                                                                                           |
| ----------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Valid and reliable**              | Partial, measured         | Grounding in supplied text; citation verification; eval suites with published pass rates. Correctness is explicitly not claimed                                                                                                    |
| **Safe**                            | Strong for this context   | The tool makes no automated decision about a person; worst realistic harm is a wrong document, which review is designed to catch                                                                                                   |
| **Secure and resilient**            | Strong, with named gaps   | [Commitments](commitments.md) with enforcing tests; five scanners in CI; measured injection resistance; planner gap documented                                                                                                     |
| **Accountable and transparent**     | Strong                    | Per-document owner; model ledger; version provenance; prompts are open source; measurements published including failures                                                                                                           |
| **Explainable and interpretable**   | Strong for the output     | Every statement carries control attribution, so a reader can trace any sentence to its source. The model's *reasoning* is not explained, and is not relied on                                                                      |
| **Privacy-enhanced**                | Structural, with a caveat | Content ceilings; metadata-only ledger; local-model support end to end. **Classification is by provenance, not contents** — see [what must never go in](responsible-ai-use.md#what-must-never-go-in)                               |
| **Fair, with harmful bias managed** | Partial, and named        | No decisions about people are made. The real risk is register bias — procedures assuming a large, well-resourced, US-centric enterprise. Documented in [bias, fairness and labour](responsible-ai-use.md#bias-fairness-and-labour) |

## EU AI Act positioning

**This is our reading, offered so your counsel has something to confirm or
correct. It is not legal advice.**

- **Not high-risk under Annex III.** PolicyForge drafts internal security
  documentation. It does not touch biometrics, critical infrastructure
  safety components, education or employment decisions, access to essential
  services, law enforcement, migration, or administration of justice.
- **GPAI obligations sit with the model provider**, not with this tool. You
  are a deployer of a general-purpose model through an API; the provider
  carries the model-level obligations.
- **Transparency is trivially satisfied.** The tool is a CLI operated by the
  person reading its output, and every generated document is intended for
  human review before use. Where you publish generated documents internally,
  [disclosure to assessors](responsible-ai-use.md#disclosure-to-assessors)
  recommends saying so in writing regardless of obligation.
- **You should still inventory it.** Whatever the classification, an AI
  system that drafts your control documentation belongs in your AI inventory
  with a named owner. The [system card](system-card.md) is written to be
  pasted into one.

## ISO/IEC 42001 framing

The same caution as above, more briefly: 42001 certifies an **AI management
system**, which is an organizational construct. This tool is a controlled
input to yours. What it contributes is documented intended use, a risk
register with residual risk, third-party enumeration, an operating log, and
an evaluation record — which map onto several 42001 Annex A controls but do
not constitute conformity with any of them. Anyone telling you a tool is
"42001 certified" is describing something that does not exist.

## Related

- [Commitments](commitments.md) — the eight promises, each with its test
- [Security architecture](security-architecture.md) — boundaries and residual risk
- [OWASP LLM Top 10](owasp-llm-top-10.md) — the security-specific mapping
- [System card](system-card.md) — the one-page version

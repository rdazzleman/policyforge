# PolicyForge system card

The one-page version, for an AI inventory, a vendor review, or a procurement
questionnaire. Everything here is expanded elsewhere in [docs/](README.md).

## What it is

|              |                                                                                                                                                                                                                                   |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Name**     | PolicyForge                                                                                                                                                                                                                       |
| **Version**  | 1.0.0                                                                                                                                                                                                                             |
| **Licence**  | Apache-2.0                                                                                                                                                                                                                        |
| **Type**     | Single-user command-line tool, run by you, on your machine or CI runner                                                                                                                                                           |
| **Service?** | No. No server, no account, no telemetry, no data received by the maintainers                                                                                                                                                      |
| **AI used**  | A general-purpose LLM via an API you configure and hold the key for. No model is trained, fine-tuned, or shipped                                                                                                                  |
| **Autonomy** | Limited. An MCP server exposes seven **read-only** tools an external agent can call; the list is closed and a test fails if any module under `mcp/` can reach a write path. All writing stays in the CLI behind an explicit apply |

## What it is for

Turning overlapping security control catalogs — HITRUST CSF, the HIPAA
Security Rule, NIST 800-53, FedRAMP, ARC-AMPE, GovRAMP — into policies,
standards and procedures an engineer can execute, organized so every topic
has one accountable owner, with every statement traceable to the controls it
came from.

**Intended users:** security, compliance and GRC practitioners who are
competent to review what it drafts. It is a drafting aid for an expert, not
a substitute for one.

## What it must not be used for

- **Producing documentation to imply a control exists when it does not.**
- **Generating evidence.** Documents are not evidence of operation.
- **Answering an assessor or a questionnaire with unreviewed output.**
- **Back-dating** a document to imply it governed past behaviour.
- **Redistributing licensed catalog content.**
- **Processing PHI, credentials, or personal data.** Nothing in it needs
  them, and content classification is by provenance, not by contents.

## How it works, briefly

Ingest published catalogs → crosswalk them against a NIST anchor → merge and
deduplicate per topic with an LLM, grounded strictly in supplied control text
→ draft Policy / Standard / Procedure documents with control attribution on
every statement → check offline → publish to Confluence on an explicit apply.

The model is asked to **reconcile text it is given**, never to recall what a
framework says.

## What the model is not allowed to decide

Parameter values, topic ownership, control scoping, whether a document is
correct, whether it gets published, and whether the organization is
compliant. Each of those is held by a person, recorded where it can be
defended to an assessor.

## Evaluation

Prompts are graded against eval suites; results, including regressions and
reversions, are published per configuration epoch in
[MEASUREMENTS.md](../MEASUREMENTS.md) with model, repeat count, cost and
per-case pass rates. Security properties are measured the same way — prompt
injection resistance is a pass rate per model, which is why model choice is
treated as a security control on the edit path. Unmeasured paths are
recorded as unmeasured.

## Known limitations

1. **It does not make documents correct, only checkable.** Human review is
   the control the design depends on.
1. **Prompt injection is mitigated and not solved.** The fence is measured;
   the corpus scanner is a word list; the planner gap is open and documented.
1. **Model choice matters for security**, not just cost, on the edit path.
1. **Entailment checking is off by default, answering-only and
   unmeasured**, so outside that path a statement can cite a real passage
   that does not support it.
1. **Native citations are provider-dependent and unmeasured.**
1. **Content classification is by provenance, not contents.**
1. **Register bias:** generated procedures can assume a larger, better
   resourced, US-centric organization than yours.
1. **The generated-parser gate is not a sandbox.**

Full list with severity: [residual risk](security-architecture.md#residual-risk).

## Data handling

|                       |                                                                                                                                  |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Sent to the model** | Catalog text in scope, your company context, the document being drafted or edited, retrieved passages                            |
| **Never sent**        | Credentials, the ledger, version history, files outside the working tree                                                         |
| **Licensed content**  | Restricted to a local model by default; raising that is an explicit config change                                                |
| **Stored locally**    | Generated documents, version history, and a model-call ledger holding metadata and a prompt hash — never prompt or reply content |
| **Third parties**     | Only those you configure. See [Subprocessors](subprocessors.md)                                                                  |

## Security summary

Eight [commitments](commitments.md), each enforced by a named test, covering
outbound endpoints, credential containment, content ceilings, ledger
contents, licensed-content handling, model-written code, publication gates,
and traceability. CI runs ruff, gitleaks, pip-audit, bandit, semgrep,
mdformat and pytest on every push, CodeQL `security-extended` weekly, and
OpenSSF Scorecard for third-party grading of repository practice.

Report vulnerabilities via [SECURITY.md](../SECURITY.md).

## Governance mapping

- [OWASP Top 10 for LLM Applications](owasp-llm-top-10.md)
- [NIST AI Risk Management Framework](nist-ai-rmf.md), including EU AI Act
  positioning and ISO/IEC 42001 framing
- [Responsible AI use](responsible-ai-use.md)

## Statement for an AI inventory

> PolicyForge is a self-hosted, non-autonomous LLM-assisted authoring tool
> used to draft internal security compliance documentation from published
> control catalogs. It makes no automated decision about any individual.
> All output is reviewed and owned by a named accountable person before use.
> Model inference is performed by *[provider]* under our agreement with
> them; no PHI or personal data is submitted. Residual risks and their
> owners are documented by the vendor and accepted by *[owner]* on
> *[date]*.

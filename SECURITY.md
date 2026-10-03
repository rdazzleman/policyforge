# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities through **GitHub's private vulnerability
reporting** on this repository (Security → Report a vulnerability, or
directly at
<https://github.com/rdazzleman/policyforge/security/advisories/new>). That
keeps the report private until a fix exists.

**Please do not open a public issue for a security problem**, and please do
not include real organizational content, credentials, or patient data in a
report — a redacted reproduction is always sufficient.

Include, where you can: what you ran, what happened, what you expected, and
which model and provider were configured. For anything involving prompt
injection or model behaviour, **the poisoned input and the model it worked
on** are the two things that make a report actionable, since behaviour on
these paths varies by model. See
[MEASUREMENTS.md](MEASUREMENTS.md) for how findings of that kind get
recorded.

### What to expect, and when

This is a small project without a funded security team, so these are
intentions rather than contractual guarantees — but they are specific enough
to hold anyone to.

| Stage                                                | Target                                         |
| ---------------------------------------------------- | ---------------------------------------------- |
| Acknowledgement that the report arrived              | 7 days                                         |
| An assessment: reproduced or not, and the severity   | 14 days                                        |
| A fix on `main`, or a written explanation of why not | 90 days                                        |
| Public disclosure                                    | After a fix, or at 90 days by mutual agreement |

If a report cannot be fixed — because it is a property of the design rather
than a defect — it gets written into the residual-risk section of
[docs/security-architecture.md](docs/security-architecture.md) under its own
heading rather than quietly closed. Several items already there arrived that
way. Credit is given in the CHANGELOG unless you ask otherwise.

If you believe a report is being mishandled or has gone quiet past these
targets, say so in the thread; there is no separate escalation path to find.

## Scope

**In scope:**

- Anything that sends content past its configured boundary ceiling — that is
  the tool's central guarantee. See
  [`llm/boundary.py`](src/policyforge/llm/boundary.py).
- Prompt injection that survives the fence, or corpus text that steers the
  planner on the edit path. Note the **known** planner gap, documented under
  [residual risk](docs/security-architecture.md#residual-risk) — a new
  instance of the known gap is useful, but is not a new finding.
- Anything that escapes `parser_gate`'s static check *and* its audit hook to
  reach the filesystem, network, or a subprocess.
- Credential or licensed-content leakage into the ledger, version history,
  generated output, or any committed path.
- A publish path that destroys a live wiki page the guards should have
  protected.
- The usual: injection, path traversal, unsafe deserialization, dependency
  vulnerabilities with a demonstrated path.

**Out of scope:**

- **Model output being wrong.** The tool drafts documents that a person must
  review; inaccuracy is the documented operating condition, not a
  vulnerability. Systematic grounding failures are interesting — report them
  as bugs with a reproduction.
- **`parser_gate` not being a sandbox.** It is documented as not being one. A
  bypass of a specific check is in scope; the general observation is not.
- **The injection scanner missing a phrasing.** It is a word list by design
  and documented as one. A phrasing that is *common* in real corpora is worth
  reporting.
- Vulnerabilities in your model provider, in Confluence, or in a
  self-hosted model you run.
- Anything requiring an attacker to already have code execution as the user
  running the tool. PolicyForge runs with exactly that user's privileges by
  design.
- Findings from an automated scanner with no demonstrated path.

## Supported versions

This project is developed on `main` and does not currently publish tagged
releases with separate support windows. Fixes land on `main`. If you are
running a fork, rebase it.

## What this project already runs

Reports that duplicate a check already in CI are usually already known. On
every push and pull request: ruff, gitleaks (full history), pip-audit,
bandit, semgrep (`p/python`, `p/security-audit`, `p/owasp-top-ten`),
mdformat, and pytest. CodeQL `security-extended` runs on `main` and weekly.
Dependabot watches dependencies and SHA-pinned actions with a seven-day
cooldown.

Run the whole set locally with `python scripts/check.py`.

## Further reading

- [System card](docs/system-card.md) — the one-page summary
- [Commitments](docs/commitments.md) — eight promises, each with its
  enforcing test. **Start here if you are trying to work out whether
  something is a bug or a documented design limit**
- [Security architecture](docs/security-architecture.md) — trust boundaries,
  data classification, the untrusted-input inventory, and residual risk
- [OWASP Top 10 for LLM Applications mapping](docs/owasp-llm-top-10.md)
- [NIST AI RMF alignment](docs/nist-ai-rmf.md)
- [Subprocessors and data flow](docs/subprocessors.md)
- [Responsible AI use](docs/responsible-ai-use.md)
- [CONTRIBUTING.md](CONTRIBUTING.md) — the paths where a change is most
  likely to break a commitment

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
[Measurements](https://zardoz.io/trust/measurements/) for how findings of that kind get
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
[the security architecture](https://zardoz.io/trust/security-architecture/#residual-risk) under its own
heading rather than quietly closed. Several items already there arrived that
way. Credit is given in the CHANGELOG unless you ask otherwise.

If you believe a report is being mishandled or has gone quiet past these
targets, say so in the thread; there is no separate escalation path to find.

## Supported versions

Only the latest minor release is supported. Security fixes ship in the
next minor release; older releases don't get backported fixes.

## More

- **Scope, what is out of scope, and what CI runs:** [zardoz.io/trust/security](https://zardoz.io/trust/security/)
- **All of the security and trust documentation:** [zardoz.io/trust](https://zardoz.io/trust/)

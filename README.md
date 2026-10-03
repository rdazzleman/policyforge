# PolicyForge

**Security compliance documentation for healthcare organizations running
HITRUST and a NIST-based program at the same time.**

PolicyForge turns overlapping control catalogs (NIST 800-53, FedRAMP,
ARC-AMPE, the HIPAA Security Rule, HITRUST CSF) into policies, standards and
procedures an engineer can execute. Every topic has one accountable owner. It
uses an LLM you bring the API key for, grounded in the control text you
supply rather than in the model's memory of a framework.

**Documentation, guides and support: [zardoz.io](https://zardoz.io).**

## Install

With Homebrew (macOS and Linux):

```bash
brew install rdazzleman/tap/policyforge
```

Without Homebrew:

```bash
pipx install git+https://github.com/rdazzleman/policyforge@v1.7.0
```

The optional extras (`bedrock`, `vertex`, `litellm`, `mcp`) aren't in the
Homebrew formula. With pipx, add them as `policyforge[mcp] @ git+…`.

## Quick start

Lay out a project, then draft one Standard:

```bash
policyforge init my-policies && cd my-policies

cp config/config.example.yaml config/config.yaml   # provider, model, and the NAME of the env var holding your key
cp config/topics.example.yaml config/topics.yaml   # starter topics, each with an owning team
export ANTHROPIC_API_KEY=sk-...                    # whichever variable your config names
policyforge llm-check                              # confirms the key works

# Cross-reference the bundled catalogs. No model calls.
policyforge map --controls data/frameworks/nist-800-53-r5/controls.json \
                --controls data/frameworks/hipaa-security-rule/controls.json \
                --controls data/frameworks/fedramp/controls.json \
                --controls data/frameworks/arc-ampe/controls.json

# Merge every control this topic owns into one set of requirements.
policyforge synthesize --topic-name "Identity Lifecycle & Access Review" \
                       --controls data/frameworks/nist-800-53-r5/controls.json \
                       --controls data/frameworks/hipaa-security-rule/controls.json

# Draft the Standard from that synthesis.
policyforge generate --tier standard \
                     --synthesis output/synthesis/identity-lifecycle-access-review.md
```

You get a Standard in `output/standards/`, its synthesis in
`output/synthesis/`, the version history in `output/.history/`, and a record
of every model call in `policyforge model-log`.

Roles nobody has filled in stay as visible placeholders, such as
`[Identity Provider]`, rather than guesses. `policyforge roles` lists them;
fill them in under `org:` in `config/config.yaml` and generate again.

## What you may redistribute

Each catalog's own README says where it came from and how it may be cited.

| Framework                                   | Licence                           | In this repository                                |
| ------------------------------------------- | --------------------------------- | ------------------------------------------------- |
| NIST SP 800-53 Rev 5                        | US government work, public domain | `data/frameworks/nist-800-53-r5/`                 |
| NIST SP 800-171 Rev 3                       | US government work, public domain | `data/frameworks/nist-800-171-r3/`                |
| NIST CSF 2.0                                | US government work, public domain | `data/frameworks/nist-csf-2-0/`                   |
| NIST AI RMF 1.0                             | US government work, public domain | `data/frameworks/nist-ai-rmf/`                    |
| NIST AI RMF Playbook                        | US government work, public domain | `data/frameworks/nist-ai-rmf-playbook/`           |
| FedRAMP                                     | US government work, public domain | `data/frameworks/fedramp/`                        |
| ARC-AMPE                                    | Published by CMS, public domain   | `data/frameworks/arc-ampe/`                       |
| HIPAA Security Rule                         | Federal regulation, public domain | `data/frameworks/hipaa-security-rule/`            |
| HIPAA Privacy Rule                          | Federal regulation, public domain | `data/frameworks/hipaa-privacy-rule/`             |
| HIPAA Breach Notification Rule              | Federal regulation, public domain | `data/frameworks/hipaa-breach-notification-rule/` |
| ONC certification criteria (45 CFR 170.315) | Federal regulation, public domain | `data/frameworks/cfr-170-315-onc-certification/`  |
| Information blocking (45 CFR 171)           | Federal regulation, public domain | `data/frameworks/cfr-171-information-blocking/`   |
| SUD patient records (42 CFR Part 2)         | Federal regulation, public domain | `data/frameworks/cfr-42-part-2-sud-records/`      |
| HITRUST CSF, HITRUST AI                     | Licensed by HITRUST               | Never bundled: bring your own                     |
| GovRAMP                                     | No redistribution grant found     | Never bundled: bring your own                     |
| SOC 2 Trust Services Criteria               | Licensed by the AICPA             | Never bundled: bring your own                     |

The loaders for the bring-your-own frameworks are in the licensed-framework
plugin, which is distributed separately from the public PolicyForge.

## More

- **Guides, configuration and everything else:** [zardoz.io](https://zardoz.io)
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Reporting a vulnerability:** [SECURITY.md](SECURITY.md)
- **How the project uses AI:** [docs/responsible-ai-use.md](docs/responsible-ai-use.md) and [docs/system-card.md](docs/system-card.md)
- **License:** [LICENSE](LICENSE) (Apache 2.0)

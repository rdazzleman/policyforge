# Subprocessors and data flow

Every party that can receive your content when you run PolicyForge, and what
you must confirm about each one yourself.

## Read this first

**PolicyForge has no subprocessors of its own.** It is software you run; the
maintainers operate no service, receive no data, and cannot see anything you
generate. There is no account, no license server, and no telemetry — a
commitment enforced by
[`tests/test_no_undeclared_endpoints.py`](../tests/test_no_undeclared_endpoints.py),
listed as [C-01](commitments.md#c-01--nothing-is-contacted-that-the-operator-did-not-configure).

What follows is therefore not a list of *our* subprocessors. It is a list of
**the parties you may choose to introduce**, so that your vendor register and
your data-flow diagram can be accurate. Every one of them is something you
configured.

**We cannot attest to any third party's terms.** Retention periods, training
use, sub-processing, and BAA availability are between you and them, and they
change. The "you must confirm" column is the actual deliverable of this page.

## Model providers

Exactly one is active at a time, named by `llm.provider` in your config.

| `provider`      | Who receives content                                                    | Class                       | You must confirm                                                                                                                                                                                      |
| --------------- | ----------------------------------------------------------------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `anthropic`     | Anthropic                                                               | third-party                 | Retention, training use, whether a BAA is available and executed                                                                                                                                      |
| `bedrock`       | AWS (your account's region)                                             | third-party                 | Your AWS agreement and BAA; Bedrock's model-provider terms for the specific model                                                                                                                     |
| `gemini`        | Google (AI Studio API)                                                  | third-party                 | **Which tier your key is on.** Google's terms have historically treated free and paid keys differently on whether prompts may be used to improve their products, and those terms are theirs to change |
| `vertex`        | Google Cloud (your project)                                             | third-party                 | Your GCP agreement and BAA; Vertex terms for the specific model                                                                                                                                       |
| `litellm`       | Whatever LiteLLM routes to, which may itself route to further providers | third-party                 | **Two hops.** OpenRouter's terms *and* the terms of whichever upstream serves your model. This is the least transparent option                                                                        |
| `openai-compat` | Whatever endpoint you set in `base_url`                                 | inferred from the URL       | Where that endpoint actually runs, and who operates it                                                                                                                                                |
| `local`         | Nothing leaves the host                                                 | local                       | Nothing                                                                                                                                                                                               |
| `cascade`       | Both halves of the cascade                                              | the more exposed of the two | Both, on the terms above                                                                                                                                                                              |

A note on `litellm`/OpenRouter, because it is the configuration most people
start with and the one with the longest chain: content may traverse two
organizations before reaching a model, and a route can change without your
config changing. If your content classification matters, this is the
provider to scrutinize first.

## Other channels

| Channel                                                                                                            | Who receives content                                                                                                | Default                                      | You must confirm                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Confluence**                                                                                                     | The Atlassian instance you name                                                                                     | Off — no host is configured by default       | That your instance is where you think it is (Cloud vs. Data Center), and the token's scope                                                                                                                                                         |
| **GitHub wiki** (`publish.github_wiki`)                                                                            | GitHub, as a git remote (`github.com`) — the wiki repository you name                                               | Off — no repository is configured by default | That the repository is private if your documents are internal. `publish` refuses a public wiki without `--allow-public`, treats unknown visibility as public, and never publishes licensed catalog content to a public wiki whatever the flag says |
| **Embedder** (`embed.base_url`)                                                                                    | Whatever endpoint you set                                                                                           | **Off; loopback when enabled**               | If pointed off-host: where it runs. Boundary-enforced and ledger-recorded like any model                                                                                                                                                           |
| **Reranker** (`rerank.base_url`)                                                                                   | Whatever endpoint you set                                                                                           | **Off; loopback when enabled**               | Same                                                                                                                                                                                                                                               |
| **Catalog fetches** (`etl-oscal`, `etl-hipaa`, `etl-hipaa-crosswalk`, `etl-fedramp`, `etl-arc-ampe`, `etl-ai-rmf`) | NIST (`csrc.nist.gov`, `airc.nist.gov`, `raw.githubusercontent.com`), eCFR (`www.ecfr.gov`) and CMS (`www.cms.gov`) | Explicit command only                        | Nothing — these are **outbound requests for public government works.** No content of yours is sent                                                                                                                                                 |

The embedder and reranker are called out because they are the two channels
that would otherwise carry your policy corpus somewhere unclassified and
unrecorded. [`llm/channel.py`](../src/policyforge/llm/channel.py) exists
specifically to hold them to the same ceiling as a model, and
[`tests/test_side_channels.py`](../tests/test_side_channels.py) asserts text a
ceiling forbids never reaches the endpoint.

## The MCP server introduces no recipient, but a new trigger

Worth separating, because the two get conflated. The MCP server
(`policyforge mcp`) exposes seven read-only tools to an external agent. It
**adds no party to this page**: nothing in it fetches, every tool reads the
corpus and catalogs already on disk, and the transport is stdio — a local
subprocess, no port, no remote connection.

One of those tools, `ask_documents`, answers from the corpus, and if a model
is configured it calls that model to write the grounded answer. Same
provider, same ledger, same boundary check as the CLI. So:

- **No new recipient.** Your content goes nowhere it was not already going.
- **A new trigger.** A model call can now originate from an agent rather
  than from a person typing a command.

If your vendor register records *when* a processor is invoked rather than
only *which* processors exist, that distinction belongs in it.
`policyforge model-log` shows the calls either way.

## What actually gets sent

Worth stating plainly, because "sends your data to an AI provider" is both
true and uselessly vague:

| Sent                                           | Not sent                                                                           |
| ---------------------------------------------- | ---------------------------------------------------------------------------------- |
| Control catalog text for the controls in scope | Your credentials ([C-02](commitments.md#c-02--no-credential-ever-reaches-a-model)) |
| Your company context, if you supplied it       | Anything from `output/` unless it is the document being edited                     |
| The topic and document being drafted           | The model ledger                                                                   |
| For edits: the live page being rewritten       | Version history                                                                    |
| For Zardoz: the retrieved passages             | Files outside the working tree                                                     |

Licensed catalog content (HITRUST, GovRAMP) is **not** sent to a third party
unless you raise its ceiling, which requires an explicit config change.

## Keeping content in your boundary

Two supported configurations, in increasing order of restriction:

```yaml
# Everything of your own stays on your network; public catalogs may go out.
llm:
  boundary:
    organization-internal: self-hosted
```

```yaml
# Nothing leaves the host at all. Requires a local model.
llm:
  provider: local
  base_url: http://localhost:11434/v1
  boundary:
    public-domain: local
    organization-internal: local
```

Verify either with **`policyforge boundary`** before running anything, and
audit what actually happened with **`policyforge model-log`**. Those two
commands are how you evidence this section to an assessor rather than citing
this page.

## For your vendor register

Copy-paste starting point. Delete the rows you do not use.

> **PolicyForge** (Apache-2.0, self-hosted CLI). No vendor relationship; no
> data received by the maintainers; no telemetry. Introduces the following
> processors, each configured by us: *[model provider]* for document
> drafting; *[Atlassian instance]* for publication. Content classes sent:
> public-domain catalog text and organization-internal drafting context.
> Licensed catalog content is restricted to a local model by default.
> Evidence: `policyforge boundary`, `policyforge model-log`.

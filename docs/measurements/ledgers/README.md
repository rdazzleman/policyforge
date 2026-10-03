# Surviving ledgers

The primary data behind every figure in `MEASUREMENTS.md` that can still be
re-derived. Preserved here by #207 on 2026-09-24. **Before this, each lived in
one session's scratch directory and nowhere else**, which is how most of the
file's history lost its source.

Each row is one model call, or one topic in the 2026-09-21 file, as the
project's ledger (`src/policyforge/llm/ledger.py`) or the partial-matrix
runner wrote it. The rows hold **metadata only**: model, site, subject, token
counts, cost, stop reason, a prompt hash and the provider's request id. No
prompt text, no generated text, no catalog content.

| file                                              | rows | what it is                                                                                                         | backs                                                        |
| ------------------------------------------------- | ---- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| `2026-09-17-policy-set-glm.jsonl`                 | 80   | 20 topics × synthesize + three tiers, `z-ai/glm-5.3-flash` via OpenRouter, on 1.2.1's code                         | epoch 21                                                     |
| `2026-09-17-policy-set-glm-before-1.2.1.jsonl`    | 80   | the same run on the code before 1.2.1, with 15 truncated calls                                                     | epoch 21, "What 1.2.1 did"                                   |
| `2026-09-17-policy-set-sonnet-5.jsonl`            | 25   | 5 topics, `anthropic/claude-sonnet-5` **via OpenRouter**, not the direct API                                       | epoch 21                                                     |
| `2026-09-18-evals-glm-deepseek-a.jsonl`           | 505  | eval-harness calls, glm and deepseek-v4-flash, plus 2 sonnet-5 calls                                               | no written epoch                                             |
| `2026-09-18-evals-glm-deepseek-b.jsonl`           | 136  | the same                                                                                                           | no written epoch                                             |
| `2026-09-19-evals-glm-deepseek-c.jsonl`           | 178  | the same                                                                                                           | no written epoch                                             |
| `2026-09-21-document-set-sonnet-4.5.jsonl`        | 25   | one row **per topic**, not per call: synthesize + Standard, `claude-sonnet-4.5` via OpenRouter                     | epoch 24                                                     |
| `2026-09-24-synthesize-glm-paired.jsonl`          | 3    | three synthesize calls paired by topic with epoch 21                                                               | epoch 23's cost correction, and "How an epoch is registered" |
| `2026-09-28-epoch25-deepseek-evals.jsonl`         | 754  | the 13 eval suites x3, deepseek                                                                                    | epoch 25                                                     |
| `2026-09-28-epoch25-deepseek-pipeline.jsonl`      | 103  | 25 topics × synthesize + three tiers, `deepseek-v4-flash` via OpenRouter; 22 written whole                         | epoch 25                                                     |
| `2026-09-28-epoch25-eval-summaries.json`          | –    | each eval step's own summary line, verbatim, per model and suite                                                   | epoch 25's eval table                                        |
| `2026-09-28-epoch25-glm-evals.jsonl`              | 727  | the 13 eval suites x3, glm                                                                                         | epoch 25                                                     |
| `2026-09-28-epoch25-glm-pipeline.jsonl`           | 102  | 25 topics × synthesize + three tiers, `z-ai/glm-5.3-flash` via OpenRouter                                          | epoch 25                                                     |
| `2026-09-28-epoch25-opus-evals.jsonl`             | 261  | the 13 eval suites x1, Opus                                                                                        | epoch 25                                                     |
| `2026-09-28-epoch25-opus-pipeline.jsonl`          | 32   | 5 topics × synthesize + three tiers, `claude-opus-5.5` via OpenRouter                                              | epoch 25                                                     |
| `2026-09-28-epoch25-run-record.jsonl`             | 111  | one line per step with the guard's spend before and after, plus each start line (cap, commit, the user's approval) | epoch 25                                                     |
| `2026-09-28-epoch25-sonnet-evals.jsonl`           | 257  | the 13 eval suites x1, `claude-sonnet-5`                                                                           | epoch 25                                                     |
| `2026-09-29-epoch25-issue3-glm-standard.jsonl`    | 5    | 5 Standards at a 32,768 first budget, glm                                                                          | epoch 25, #3                                                 |
| `2026-09-29-epoch25-issue3-opus-standard.jsonl`   | 5    | 5 Standards at a 32,768 first budget, opus-5.5                                                                     | epoch 25, #3                                                 |
| `2026-09-29-epoch25-issue3-run-record.jsonl`      | 16   | one line per Standard with its exit code and spend                                                                 | epoch 25, #3                                                 |
| `2026-09-29-epoch25-issue3-sonnet-standard.jsonl` | 5    | 5 Standards at a 32,768 first budget, sonnet-5                                                                     | epoch 25, #3                                                 |

Beside them, the three epoch-21 runs' **step records** (`*-steps.json`): one
entry per topic and step, with its seconds, whether it succeeded and any
error, plus the run's `wall_seconds`. The wall-clock figures in epoch 21 (103
and 37 minutes) come from these, not from the call ledgers. A ledger's
first-to-last timestamp span is a different instrument and reads shorter. The
glm record's one error is why that run holds 59 documents, not 60: the model
returned an empty Policy for Security Program Governance, and the pipeline
refused it by name rather than writing an empty file.

**Three of the files belong to no written epoch.** They are eval-harness runs
from 2026-09-18 and 19, preserved from another session's scratch directory.
Which work they measured is not recorded with them, so they back no figure in
`MEASUREMENTS.md` except the per-call eval rates quoted in its opening
section. They are kept because they are primary data, not because anything
depends on them.

**Epoch 25 (2026-09-28/29), added with it.** The run's own ledgers, copied from
b5's session and checked the same way: each file parses to the same rows as
its source (the sources were CRLF, and are committed as LF). The rows are
metadata only: in the call ledgers no field is longer than 120 characters,
and no row holds prompt, document or catalog text. The two run records also carry
each start line's `confirmed` field, the user's approval in the words given, which
is longer. The runs' raw step logs are **not** committed: they
carry session paths. What epoch 25 quotes from them, the eval summary lines,
is extracted verbatim into `2026-09-28-epoch25-eval-summaries.json`.

## Checked before committing

- **Credentials:** a scan for key and token shapes matched ten strings, and
  all ten are false positives: nine are the topic slug
  `ri`+`sk-assessment-authorization`, and one is the topic name
  "Risk Assessment & Authorization". `request_id` values are provider
  generation ids, which are not credentials. The gate's secrets scan runs
  over these files too.
- **Licensed content:** none. No row holds text from a catalog or a
  document.
- **Line endings:** the ledgers were written with CRLF on Windows. They are
  committed as LF, and every file was checked to parse to identical rows
  before and after.

## Checksums of the committed files

```
6a6f4f0e46ca00eed355ff9be8345df70595c075e3156aceefbb6f3fb01ad363  2026-09-17-policy-set-glm-before-1.2.1.jsonl
2f557667f8293b3d72821e1cb65ddef61ee8122b279c2814d9c589eb0c4f1191  2026-09-17-policy-set-glm.jsonl
d18433ca579f32f1378c242941fd654e53cecf4a9e4a06e2cd8846b321004aa0  2026-09-17-policy-set-sonnet-5.jsonl
2c183410eb72538b073a5a78b29715e105c50f3e099c85e8c98676a855bcd627  2026-09-18-evals-glm-deepseek-a.jsonl
837e7a4728998b7089bd1184f72199ab548f2cfa756549c5ae1540493c04df0d  2026-09-18-evals-glm-deepseek-b.jsonl
e0cfc1f98bf2dea2c5da6abd3ed114943b2d5fc7ea0092fe2610dff15ead5109  2026-09-19-evals-glm-deepseek-c.jsonl
90c1b8238ee5ef9aa2847a3040d3cfa085c5f9a856168dc7920ceabf32135fb6  2026-09-21-document-set-sonnet-4.5.jsonl
1ecd6f8e8a5a163400556c86682280f91f611f50e33807ad6132bcb6005c8ff3  2026-09-24-synthesize-glm-paired.jsonl
e995d84b70a033ae41982fa348e4cf9461d95b739934b77ecc1d5307687cb6a1  2026-09-17-policy-set-glm-before-1.2.1-steps.json
6c3008e1322964c1b3977b8903600f13dd943846af8d65a77af73412807f51d5  2026-09-17-policy-set-glm-steps.json
324e13062063263717cf9bfa262136cb9db20fb4a7635d84eb5b1bf78707235b  2026-09-17-policy-set-sonnet-5-steps.json
889087d8f794a78b3b64de3c6b2b3a1a81e4a9cfc15f50662262b2e611a46aaf  2026-09-28-epoch25-deepseek-evals.jsonl
25b8a4f4fd55e76aa25a6264413aca4d3adf3052c14556518faacd2198a5f016  2026-09-28-epoch25-deepseek-pipeline.jsonl
13a11f03a9ab4aac1eb470bea39299bca74a340b9503cfb2ad13b1f7fc3a19cc  2026-09-28-epoch25-eval-summaries.json
0158b8d43d283cd999eb39d5ed2e929d2474ead61256746206cf5992082da4ca  2026-09-28-epoch25-glm-evals.jsonl
b00ce1b3acc947c50b147d70577af3c4981c5238f02500762648812129aba36c  2026-09-28-epoch25-glm-pipeline.jsonl
aab79d4e2036dc96e9107a67b03657fffa4070f6c3c6d44b8d898b9503423ef8  2026-09-28-epoch25-opus-evals.jsonl
8183ae3c296bc3d07f36982127b3a2a8a1e45d4532de4d513cfd81da716a4530  2026-09-28-epoch25-opus-pipeline.jsonl
6c0a2aa97ecc95997123d0c20f7868b4812d096778d8b55b948566997bfd6cc3  2026-09-28-epoch25-run-record.jsonl
fb47e3f5a7817d16a7eceee533e094bf662d1e2218a67032f1d8e6708f519aa5  2026-09-28-epoch25-sonnet-evals.jsonl
c41ffc3b1445fd28ac5cc4035ecaceed7e9ac29d1858f72112cd16c0340eff10  2026-09-29-epoch25-issue3-glm-standard.jsonl
dc2a0de809f3c1e3b08f1fffe2fdd025c2773183305673131003535f682ec4d0  2026-09-29-epoch25-issue3-opus-standard.jsonl
69f58ffb92c35c836ccc90844e1302a881260c8546022e6b4a293ff75d573895  2026-09-29-epoch25-issue3-run-record.jsonl
e7783c9ac7706583b2e01c24455d1537ab4e1b1067f31a6b3576e69358ca8fba  2026-09-29-epoch25-issue3-sonnet-standard.jsonl
```

"""Zardoz: a conversational shell over the documents PolicyForge publishes.

The rest of the CLI is one-shot — a command runs a pipeline stage and exits.
Zardoz is the read side, and it is a conversation because the questions
people actually have about a policy set are follow-ups: "what's our access
review cadence?", then "who owns that?", then "does it satisfy the HIPAA
citation?". Each of those is cheap to answer and expensive to re-ask from a
cold command line.

Module map, in the order the milestones land:

    art.py       the floating head, and every persona string (M0)
    shell.py     the REPL and its command table (M0)
    corpus.py    local snapshot of the documents to answer from (M1)
    retrieve.py  find the passages that bear on a question (M2)
    answer.py    grounded answering, with citations and refusals (M3)
    conversation.py  resolving a follow-up against the turns before it (M4)

The documents themselves come from `policyforge.content`, which reads a tree
of markdown files, and from Confluence. Markdown needs no credentials, so
everything from retrieval onwards can be built and tested without a wiki.

Two rules hold across all of them:

* **Answers are grounded or absent.** Every claim cites the document and
  section it came from, and "the documents do not say" is an expected
  outcome rather than a failure. A confidently wrong answer about what your
  own policy requires is worse than no answer, because somebody acts on it.
* **Zardoz never writes.** It can draft a `policyforge edit-topic` command
  for you to run, but the publish path with its gates is not in this
  package's import graph.
"""

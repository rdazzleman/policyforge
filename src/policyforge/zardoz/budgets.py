"""Output budgets for Zardoz's three short model calls.

These were once literals — 64 for routing, 150 for expansion, 200 for
resolution — sized for a model that starts answering immediately: one word,
a list of terms, one rewritten sentence. Sixty-four tokens is generous for
one word and absurd for one word preceded by four hundred tokens of
deliberation.

Two things were measured that made those numbers wrong.

**A tight budget does not degrade an answer, it deletes one.** A reasoning
model spends the budget deliberating and returns nothing.
`deepseek-v4-pro` exhausted 200 tokens on resolution, exhausted 1600 on the
retry, and never began a reply. Graded at the old budgets it scored 95% on
resolution and 89% on expansion, which reads as a model that is worse at
the task. Given room it scores 100% and 100%. It was never worse at the
task; it was never allowed to finish. Every reasoning model tried showed
the same shape, and reading that as a verdict on reasoning models was the
wrong conclusion drawn from the right data.

**A tight budget costs more, not less.** A truncation triggers a retry at
eight times the ceiling (`llm/_inline_thinking.retry_budget`), so a budget
that forces one bills two requests and the second is far larger. Raising
these *lowered* spend on both models tested: `deepseek-v4-pro` from $0.0934
to $0.0607, and `deepseek-v4-flash` — which never visibly failed at the old
budgets and was quietly paying the retry tax anyway — from $0.0052 to
$0.0013, a four-fold drop for identical scores.

The values below are the ones that measurement settled on. They cost a
model that does not need the room nothing, because `max_tokens` is a
ceiling and not a target: the control run proved it by scoring identically
at both budgets while spending a quarter as much.

Overridable per run so a new model's needs can be tested without editing
code. Read once at import, so a run gets one set of budgets — which is what
keeps two models in a sweep comparable.
"""

from __future__ import annotations

import os


def _budget(name: str, default: int) -> int:
    """An override from the environment, or the default.

    A bad value is refused rather than silently ignored: quietly falling
    back to the default would report a comparison the run did not perform.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number of tokens, not {raw!r}") from None
    if value < 1:
        raise ValueError(f"{name} must be at least 1 token, not {value}")
    return value


#: One word, after however much preamble the model needs to choose it.
ROUTING_TOKENS = _budget("POLICYFORGE_ROUTING_TOKENS", 800)

#: A short list of terms a document might use. `parse_expansion` truncates
#: to MAX_EXPANSION_TERMS afterwards, so a generous ceiling here buys the
#: model room to think rather than a longer list.
EXPANSION_TOKENS = _budget("POLICYFORGE_EXPANSION_TOKENS", 1500)

#: One rewritten question that stands on its own.
RESOLUTION_TOKENS = _budget("POLICYFORGE_RESOLUTION_TOKENS", 2000)

#: A grounded answer with a citation on every claim.
#:
#: 2048 since 1.3.0. Was 1024, and sized too close: in the truncation
#: re-measure (200 calls at 553d430) deepseek-v4-flash used 1019 and 967 of
#: those 1024 output tokens on two answering cases
#: (a-contradiction-planted-by-a-rider-is-still-surfaced and
#: an-injected-instruction-does-not-suppress-citations) and glm-5.3-flash
#: at most 515. Nothing was cut off, but two cases within five tokens of
#: the cap is a budget that fails on the next paraphrase. Doubled; a cut-off
#: answer is refused rather than shown since 1.2.1, so the cost of being
#: wrong here is a refusal, not a silent half-answer, and the cost of the
#: headroom is nothing unless a model uses it.
ANSWERING_TOKENS = _budget("POLICYFORGE_ANSWERING_TOKENS", 2048)

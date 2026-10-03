"""Entailment judged by a language model, with the verdict constrained to
an enum.

Structured output is what makes this tolerable rather than merely
plausible. The judgement is three labels and a sentence of reasoning, and a
schema guarantees the label is one of the three — so the parsing failures
that make LLM-as-judge flaky ("Entailed.", "I'd say entailed", a paragraph
of hedging) cannot occur. `route()` uses the same mechanism for the same
reason.

**Use a different model than the one that wrote the answer.** This is the
whole reason `model` is configurable. A model asked to check its own work
shares every blind spot it had while producing it, and will wave through
exactly the claims it was disposed to make — which is the failure mode
`evals/runner.py` warns about, at its purest. A dedicated NLI cross-encoder
would be better still, being a different architecture rather than merely a
different checkpoint; see `base.py` on why that is not the first
implementation.

The prompt is deliberately narrow. It is not asked whether the claim is
true, or sensible, or good policy — only whether *this passage* carries it.
A judge that reaches for what it knows about access control has stopped
measuring grounding, which is the same mistake rule 2 of the answering
prompt guards against.
"""

from __future__ import annotations

import json

from policyforge.llm.prompts import Prompt, register

from .base import CONTRADICTED, ENTAILED, NEUTRAL, Entailer, Verdict

SYSTEM_PROMPT = register(
    Prompt(
        name="entail.judge",
        version=1,
        text="""You decide whether a passage from an organization's own \
policy documents supports a claim someone has made about it.

You are given a PASSAGE and a CLAIM. Answer with one label:

- entailed: the passage states the claim, or states something that plainly
  includes it. Different wording is fine; the same fact is the test.
- neutral: the passage neither states nor denies the claim. This is the
  common case for a wrong claim — a claim about an actor, a document or a
  requirement the passage never mentions is neutral, not contradicted.
- contradicted: the passage says something that cannot be true at the same
  time as the claim. Different actors, different intervals, different
  thresholds.

Rules:

1. Judge only against the passage. Whether the claim is true of the world,
   or usual for a security programme, or good practice, is not the
   question and knowing the answer will mislead you here.
2. A claim that changes the actor is not entailed. "IT Asset Management
   shall generate the certificate" does not support "the Security Officer
   approves the certificate", and the difference is exactly what a reader
   would be misled about.
3. A claim that weakens or strengthens the passage is not entailed. "may
   be reviewed" does not support "must be reviewed".
4. Give one short sentence of reasoning, naming what the passage actually
   says. "The passage names IT Asset Management, not the Security Officer"
   is useful; "the claim is not supported" is not.""",
    )
)

#: Three labels, so the reply cannot be a paragraph of hedging.
SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "entailment",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "enum": [ENTAILED, NEUTRAL, CONTRADICTED]},
                "reason": {"type": "string"},
            },
            "required": ["label", "reason"],
            "additionalProperties": False,
        },
    },
}


def entailment_provider(block: dict, config: dict | None = None):
    """The judge's provider, classified and recorded like every other model call.

    It was a bare `LiteLLMProvider`, which made the entailer the one model
    call in the project that never reached the ledger and was never
    classified — sending cited passages and the claims made about them to a
    hosted vendor with no record that it had. An `llm:`-shaped block is
    built from `entail:` so `ledger.wrap` can classify it by the same rules
    (`entail.api_base` and `entail.classification` count, exactly as their
    `llm.` counterparts do), and the ledger settings are the project's own.
    """
    from ..llm.ledger import wrap
    from ..llm.litellm_provider import LiteLLMProvider

    model = block["model"]
    llm_block: dict = {"provider": "litellm", "model": model}
    for key in ("api_base", "classification"):
        if block.get(key) is not None:
            llm_block[key] = block[key]
    ledger = ((config or {}).get("llm") or {}).get("ledger")
    if ledger is not None:
        llm_block["ledger"] = ledger
    return wrap(LiteLLMProvider(model=model, api_base=block.get("api_base")), {"llm": llm_block})


class LLMEntailer(Entailer):
    """Judges entailment with a schema-constrained model call."""

    def __init__(self, *, model: str | None = None, max_tokens: int = 2000, provider=None):
        self.model = model
        # Generous on purpose. A reasoning model emits its JSON last, so a
        # tight ceiling returns deliberation that is not JSON — measured
        # three separate times elsewhere in this project.
        self.max_tokens = max_tokens

        if provider is not None:
            # Dependency injection point for tests, matching every other
            # provider here.
            self._provider = provider
            return
        if not model:
            raise ValueError(
                "entail.model is required: name a model *other* than the one writing "
                "the answers, since a model checking its own work shares its blind spots."
            )
        # Through the ledger even when built directly, so there is no way to
        # construct a judge whose calls go unrecorded short of injecting a
        # provider on purpose.
        self._provider = entailment_provider({"model": model})

    def entails(self, premise: str, hypothesis: str) -> Verdict:
        prompt = f"PASSAGE\n\n{premise.strip()}\n\nCLAIM\n\n{hypothesis.strip()}"

        if not self._provider.supports_schema():
            raise RuntimeError(
                f"{self.model} cannot be held to a schema, and an unconstrained "
                f"verdict is not worth having. Choose a model that can."
            )

        from ..llm import effort

        response = effort.call_json(
            self._provider,
            effort=effort.ENTAILMENT,
            system=SYSTEM_PROMPT,
            prompt=prompt,
            schema=SCHEMA,
            max_tokens=self.max_tokens,
            temperature=0.0,
        )
        parsed = json.loads(response.text)
        return Verdict(label=parsed["label"], reason=parsed.get("reason", ""))

    def check(self) -> bool:
        """A judgement whose right answer is not in doubt."""
        verdict = self.entails(
            "IT Asset Management shall retain records for six years.",
            "Records are retained for six years.",
        )
        return verdict.supports

"""The provider an eval run measures, built the way production builds one.

Every command obtains its provider from `policyforge.llm.base.get_provider`,
which builds the configured provider and wraps it in the call ledger. The
eval harness did not: `scripts/eval_zardoz.py --model` constructed a
`LiteLLMProvider` directly and metered that. Two construction paths meant
two request shapes, and when the ledger wrapper silently hid every
`supports_*` flag, the harness kept sending effort while the CLI did not.
Epochs 15 through 19 in `MEASUREMENTS.md` describe a request production
never made, and nothing could have said so, because nothing compared the
two paths.

So there is one path. `eval_config` turns the run's options — a model
string, a request interval — into overrides on the same config dict
production reads, and `build_provider` hands that dict to `get_provider`
and meters the result. A `--model` flag is a config override, not a second
constructor, and `tests/test_eval_provider_parity.py` holds the two paths
to the same provider class, the same flags and the same effort, caching
and grounding decisions.

**Eval calls go to their own ledger file**, `evals.jsonl` beside the
production one, and every case is recorded under `<suite>/<case>` with the
site `eval`. They are real calls that were billed and that carried the
eval corpora to a vendor, so they belong in a ledger; they are not the
organization's documents, so they do not belong in the one that answers
"which documents did that model touch".
"""

from __future__ import annotations

from copy import deepcopy

from policyforge.config import load_config
from policyforge.llm.base import get_provider
from policyforge.llm.boundary import LOCAL, classify_provider
from policyforge.llm.ledger import ledger_path

#: The file name eval runs record to, beside whatever the production ledger
#: is configured as. A name rather than a path, so a config that moved the
#: ledger keeps its evals in the same place.
EVAL_LEDGER_NAME = "evals.jsonl"

#: The site every eval call is recorded under.
EVAL_SITE = "eval"


def eval_config(
    *,
    model: str | None = None,
    min_interval: float = 0.0,
    base: dict | None = None,
) -> dict:
    """Production's config with this run's overrides applied.

    `model` replaces the `llm` block with a LiteLLM provider for that model
    string, which is what `--model` has always meant; the ledger settings
    survive, since turning the ledger off is a decision the config made.
    `min_interval` reaches the LiteLLM provider as `min_interval_seconds`
    and is ignored by any other, which has no per-request pacing to set.
    With neither, the configured provider is measured as configured.

    `base` is the config to start from; left out, `config/config.yaml` is
    read, and a missing file is fine when `model` is given, since that is
    the whole point of the flag.
    """
    if base is None:
        try:
            base = load_config()
        except FileNotFoundError:
            base = {}
    config = deepcopy(base)
    llm = dict(config.get("llm") or {})

    if model is not None:
        llm = {"provider": "litellm", "model": model, "ledger": dict(llm.get("ledger") or {})}
    elif not llm.get("provider"):
        raise FileNotFoundError(
            "No config/config.yaml with an llm block, and no --model: nothing to build a "
            "provider from."
        )
    if min_interval and llm.get("provider") == "litellm":
        llm["min_interval_seconds"] = min_interval

    ledger = dict(llm.get("ledger") or {})
    ledger["path"] = str(ledger_path({"llm": {"ledger": ledger}}).with_name(EVAL_LEDGER_NAME))
    llm["ledger"] = ledger

    config["llm"] = llm
    return config


class Metered:
    """Wraps a provider and counts what the run spent.

    A pass rate on its own does not decide between two models — the whole
    question is what each one costs to be that good. Kept here rather than
    in the provider layer because it is a property of a *run*, and only the
    thing driving the run can see the whole of one.
    """

    def __init__(self, inner, provider_class: str | None = None):
        self._inner = inner
        #: The boundary class of the configured provider, so a local model's
        #: unpriced reply counts as free (#446, the rule of #379's ruling).
        #: None when unknown, which never counts a reply as free. Given by
        #: `build_provider`; otherwise read off the ledger wrapper when there
        #: is one (the entailment judge in `eval_zardoz` is built that way).
        self._provider_class = provider_class or getattr(inner, "_provider_class", None)
        self.calls = 0
        #: The known part of the run's cost: every priced reply, and the known
        #: last attempt of a reply whose own total is unknown (#382).
        self.cost = 0.0
        #: True once any call priced itself. Distinguishes "this run was
        #: free" from "nobody reported a price", which a bare 0.0 cannot.
        self.priced = False
        #: Calls that raised. They reached the vendor and were billed, but
        #: no response came back to read a price off.
        self.unpriced = 0
        #: Calls that RETURNED with no price (#446). Until #446 these were
        #: counted in `calls` and nowhere else, so the known part stood as
        #: the total and the per-call figure was divided by them as though
        #: they were free: one priced $0.10 reply and one unpriced read
        #: "$0.1000 total, $0.05000 each" (ba's repro, from #450).
        self.returned_unpriced = 0
        #: Replies whose whole cost is known, and what they cost: the only
        #: honest base for a per-call figure.
        self.priced_calls = 0
        self.priced_cost = 0.0
        #: Every reply, in order: (stop_reason, output_tokens, model). The
        #: runner slices this per run so a report can say whether a failed
        #: run's reply was cut off — b5's re-measure had one `length` stop
        #: in 200 calls and no way to tell which run it belonged to.
        self.replies: list[tuple[str | None, int | None, str | None]] = []

    @property
    def provider(self):
        """The provider being metered — production's, ledger and all."""
        return self._inner

    def _metered(self, call, **kwargs):
        # Counted in a finally, because a call that raises was still sent and
        # still billed. Incrementing after the call instead let a model that
        # trips ReasoningBudgetExhausted report *fewer* calls and a *lower*
        # cost than one that answers cleanly — inverting the comparison the
        # meter exists to make, the same way `0.0 or None` did.
        try:
            response = call(**kwargs)
        except Exception:
            self.unpriced += 1
            raise
        finally:
            self.calls += 1
        if response.cost_usd is not None:
            self.cost += response.cost_usd
            self.priced = True
            self.priced_calls += 1
            self.priced_cost += response.cost_usd
        elif self._provider_class == LOCAL:
            # A local model is free although its endpoint reports no price.
            self.priced = True
            self.priced_calls += 1
        else:
            self.returned_unpriced += 1
            last = getattr(response, "last_cost_usd", None)
            if last is not None:
                self.cost += last
                self.priced = True
        self.replies.append(
            (
                getattr(response, "stop_reason", None),
                getattr(response, "output_tokens", None),
                getattr(response, "model", None),
            )
        )
        return response

    def generate(self, **kwargs):
        return self._metered(self._inner.generate, **kwargs)

    # Every call that can cost money is metered, not only `generate`. This
    # wrapper used to expose `generate` and `check` and nothing else, so under
    # the harness `supports_schema`, `generate_json` and `supports_effort` were
    # all missing and every caller took its fallback: routing ran its prose
    # path, argument filling and chaining were skipped, and no effort level
    # was sent. Eval runs measured the fallbacks rather than what a user of a
    # schema-capable model gets, from before schema routing existed until a
    # chaining eval scored 0/6 on questions a direct probe got 12/12.

    def generate_json(self, **kwargs):
        return self._metered(self._inner.generate_json, **kwargs)

    def generate_grounded(self, **kwargs):
        return self._metered(self._inner.generate_grounded, **kwargs)

    def __getattr__(self, name):
        """Everything else is the inner provider's: capabilities, model, counters.

        Only reached for attributes this class does not define, so the metered
        calls above always take precedence. A capability the inner provider
        lacks stays absent here too — the meter must not invent one. This
        class deliberately does not subclass `LLMProvider`: a base class
        would define every `supports_*` flag as False and put them out of
        `__getattr__`'s reach, which is precisely how the ledger wrapper hid
        them.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    def check(self) -> bool:
        return self._inner.check()

    def summary(self) -> str:
        if not self.priced:
            return f"{self.calls} model call(s); this provider does not report cost"
        unknown = self.unpriced + self.returned_unpriced
        if not unknown:
            each = self.cost / self.calls if self.calls else 0.0
            line = f"{self.calls} model call(s), ${self.cost:.4f} total, ${each:.5f} each"
        else:
            # #446, #379's rule: never the known part as the total, and a
            # per-call figure only over the calls whose cost is known.
            line = f"{self.calls} model call(s), ${self.cost:.4f} known + {unknown} of unknown cost"
            if self.priced_calls:
                each = self.priced_cost / self.priced_calls
                line += f", ${each:.5f} each over {self.priced_calls} priced"
        # A cascade's whole economic case is how rarely the cheap model
        # needed help. The counter existed and nothing printed it, which
        # made a working cascade indistinguishable from a dead one.
        escalations = getattr(self._inner, "escalations", None)
        if escalations is not None:
            primary = getattr(self._inner, "primary_calls", 0)
            share = f"{escalations / primary:.0%}" if primary else "n/a"
            line += f"; {escalations}/{primary} escalated to the stronger model ({share})"
        if unknown:
            # Said out loud rather than folded in: which kind of unknown, so a
            # reader comparing two models knows which way the figure is wrong.
            line += (
                f" ({self.unpriced} raised, billed but unpriced; "
                f"{self.returned_unpriced} returned no price)"
            )
        return line


def build_provider(config: dict) -> Metered:
    """The provider production would build from `config`, metered.

    `get_provider` and nothing else: the one way every command gets its
    provider, ledger wrapper included, so what the harness measures is what
    a user runs.
    """
    # The class comes from config, not from the provider: with the ledger
    # off, the provider is unwrapped and carries none (#446).
    klass = classify_provider(config.get("llm") or {}).klass
    return Metered(get_provider(config), provider_class=klass)

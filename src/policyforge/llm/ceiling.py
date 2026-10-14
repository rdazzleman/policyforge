"""A model's published output ceiling, and the one rule for clamping to it (#3).

The long-document budget is 32,768 tokens (`generate/policy_writer.py`), and
a re-send after an empty reply asks for eight times its first budget
(`_inline_thinking.retry_budget`). Both can exceed what a model will accept,
and a `max_tokens` above a model's cap comes back as a 400 that reads nothing
like the truncation it was trying to avoid. So a request never asks for more
than the model's published ceiling, where one is published (80's condition 1
on #3).

**Where the ceiling comes from:** LiteLLM's bundled model map
(`max_output_tokens`), with PolicyForge's override table on top, through
`litellm_map.load()`: the same table re-send prices come from (#79). It no
longer depends on the environment. Before #79, LiteLLM downloaded a newer map
at import unless `LITELLM_LOCAL_MODEL_COST_MAP` was set, and the two could
disagree: opus via OpenRouter read 128,000 downloaded and 64,000 bundled (#78).

The 13 model strings the README, docs, example config and `.claude` name,
swept on #79 with LiteLLM 1.103.0. **One reads under 32,768:** the
`gemini/gemini-2.0-flash` example the README carried until #154, at 8,192, so
its first budget IS clamped, to 8,192. For every other named string the first
budget is never clamped; the re-sends are. Opus via OpenRouter reads 128,000
(an override: OpenRouter's model list); `anthropic/claude-opus-5.5`, direct,
reads the bundled 64,000, which no override touches.

**What it cannot see:** a model the map doesn't list (`None`, so no clamp:
the request goes as the site wrote it, and a vendor that refuses it says so as
a `ProviderRejected`). Named strings it doesn't list: the
`openrouter/deepseek` example the README carried until #154, and the bare
`z-ai/glm-5.3-flash` that old ledgers record (with its `openrouter/` prefix it
has an override).

**Local models are never looked up** (`ollama/`, `ollama_chat/`, the vendors
`boundary.LITELLM_LOCAL_VENDORS` names, in any case, folded as the boundary
folds them). For them LiteLLM asks a running
server, which took 5 s a lookup on #78 (1d measured it). It asks LiteLLM's
default server, not the configured `api_base`, and answers with the model's
context window (40,960 for `qwen3:14b-pf`). A context window bounds prompt and
output together. It isn't an output ceiling, so it isn't clamped to. Ollama's
`num_ctx` is what binds there.

A lookup is cached per model for the life of the process: the map doesn't
change while it runs.
"""

from __future__ import annotations

import functools


def published_output_ceiling(model: str) -> int | None:
    """The most output tokens `model` accepts in one reply, or None if unknown.

    Unknown is not zero and not "unlimited": it means nothing is published
    where this looks, so nothing is clamped. A local model is always None,
    without asking anything (see the module docstring).
    """
    from .boundary import LITELLM_LOCAL_VENDORS

    vendor = model.split("/", 1)[0].casefold() if "/" in model else ""
    if vendor in LITELLM_LOCAL_VENDORS:
        return None
    return _looked_up(model)


@functools.cache
def _looked_up(model: str) -> int | None:
    try:
        from .litellm_map import load

        litellm = load()
    except ImportError:
        return None
    try:
        info = litellm.get_model_info(model)
    except Exception:  # noqa: BLE001 - an unmapped model is simply unknown
        return None
    ceiling = info.get("max_output_tokens")
    return int(ceiling) if ceiling else None


def clamp(budget: int, ceiling: int | None) -> int:
    """`budget`, or the ceiling if the ceiling is known and lower."""
    return min(budget, ceiling) if ceiling else budget

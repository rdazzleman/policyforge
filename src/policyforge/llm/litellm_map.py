"""The one way PolicyForge imports LiteLLM: on its bundled model map, with our overrides (#79).

**Why:** LiteLLM downloads a newer model map from raw.githubusercontent.com
when it is imported, unless `LITELLM_LOCAL_MODEL_COST_MAP=True`, in which
case it uses the map bundled with the installed package. The test suite has
always set the variable (`tests/conftest.py`, #31) and the product never did.
So every price and output ceiling a user saw came from a table downloaded
that day, which no test read. Measured on #79 with LiteLLM 1.103.0: glm-5.3-flash
at $0.50/M output fetched and $0.30/M bundled; opus via OpenRouter priced
fetched, unpriced bundled. The download is also an outbound request that
`llm/boundary.py` doesn't govern, made on every run, including a run whose
licensed content is local-only.

**The rule (80's ruling on #79, 1727):** `load()` sets the variable, then
imports LiteLLM, then registers `OVERRIDES`. Every module under `src/` that
needs LiteLLM calls `load()`; none imports it directly, and
`tests/test_litellm_map.py` fails if one does. The variable is set
unconditionally, as `conftest.py` sets it: a `False` in the user's
environment doesn't turn the download back on.

**What it costs:** the bundled map ages with the LiteLLM version, and prices
move when the lock is bumped, which is a reviewed change. Where the bundled map
is wrong or silent for a model we name, `OVERRIDES` corrects it, with its
source and the date it was read. The table is re-read at each minor release
(on the cut checklist); nothing goes red on a date alone.

**What it cannot do:** pin a map for a process that imported LiteLLM before
PolicyForge did. The variable is read once, at LiteLLM's first import.
"""

from __future__ import annotations

import logging
import os
from importlib import metadata

#: LiteLLM's names for a per-token price. Kept out of the table's literals:
#: bandit reads any "*token" key beside a number as a hardcoded password.
_IN, _OUT = "input_cost_per_token", "output_cost_per_token"


def _per_million(input_usd: float, output_usd: float, **info) -> dict:
    """A LiteLLM map entry from prices in dollars per million tokens, the unit
    OpenRouter lists them in, so each figure reads against its source as is."""
    return {_IN: input_usd / 1e6, _OUT: output_usd / 1e6, **info}


#: Corrections to LiteLLM's bundled map, for models PolicyForge names where
#: that map omits or misprices them. Keyed as LiteLLM keys them. Each entry
#: carries where its figures came from, the day they were read, and what the
#: bundled map said (`was`); its `litellm` value is what gets registered.
#:
#: Source for all of these: OpenRouter's public model list,
#: https://openrouter.ai/api/v1/models (`pricing.prompt`, `pricing.completion`,
#: `top_provider.max_completion_tokens`), read 2026-09-29 by b5 on #79. The
#: bundled map is LiteLLM 1.103.0's.
#:
#: **Where OpenRouter's price moved within the day** (both DeepSeek models,
#: two reads two hours apart; 1d caught it on #89), `varies` records both
#: readings and the entry keeps the HIGHER. An override prices a worst case
#: (the re-send notice) or stands in for a missing bill, and an estimate
#: that's low half the day understates what a user may pay. Why it moves
#: wasn't measured.
#:
#: Reviewed at each minor release: re-read the source, update or delete an
#: entry, and let `tests/test_litellm_map.py` say whether each still corrects
#: something. An entry the bundled map has caught up with fails there, so a
#: redundant one is visible.
OVERRIDES: dict[str, dict] = {
    "openrouter/anthropic/claude-opus-5.5": {
        "source": "OpenRouter model list",
        "as_of": "2026-09-29",
        "was": "absent (get_model_info fell back to Anthropic's entry: 64,000 out, no price)",
        "litellm": _per_million(
            4, 20, max_output_tokens=128000, litellm_provider="openrouter", mode="chat"
        ),
    },
    "openrouter/z-ai/glm-5.3-flash": {
        "source": "OpenRouter model list",
        "as_of": "2026-09-29",
        "was": "$0.09/M in, $0.30/M out; 131,072 out",
        "litellm": _per_million(0.15, 0.50, max_output_tokens=943717),
    },
    "openrouter/deepseek/deepseek-v4-flash": {
        "source": "OpenRouter model list",
        "as_of": "2026-09-29",
        "was": "$0.03724/M in, $0.07448/M out; 384,000 out",
        "varies": "$0.0763/$0.1526 at 17:45 CDT, $0.14/$0.28 at 19:38 CDT, same list, same day",
        "litellm": _per_million(0.14, 0.28, max_output_tokens=131072),
    },
    "openrouter/deepseek/deepseek-v4-pro": {
        "source": "OpenRouter model list",
        "as_of": "2026-09-29",
        "was": "$0.422298/M in, $0.844596/M out",
        "varies": "$0.915936/$1.831872 at 17:45 CDT, $0.95526/$1.91052 at 19:38 CDT, same day",
        "litellm": _per_million(0.95526, 1.91052),
    },
    "openrouter/moonshotai/kimi-k3": {
        "source": "OpenRouter model list",
        "as_of": "2026-09-29",
        "was": "$1.70/M in, $8.50/M out",
        "litellm": _per_million(3, 15),
    },
}

_registered = False


def load():
    """LiteLLM, on its bundled map with `OVERRIDES` registered.

    Raises ImportError when LiteLLM isn't installed, exactly as `import
    litellm` would, so callers keep their own fallbacks.
    """
    global _registered
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import litellm

    if not _registered:
        # register_model warns, per entry, that a model outside the bundled
        # map has no cache pricing. That is true and not about this run.
        quiet = logging.getLogger("LiteLLM")
        level = quiet.level
        quiet.setLevel(logging.ERROR)
        try:
            litellm.register_model({key: entry["litellm"] for key, entry in OVERRIDES.items()})
        finally:
            quiet.setLevel(level)
        _registered = True
    return litellm


def basis(key: str | None) -> str:
    """Where a map figure for `key` comes from, in words a user can check."""
    entry = OVERRIDES.get(key or "")
    if entry:
        return f"PolicyForge's override table ({entry['source']}, as of {entry['as_of']})"
    try:
        version = metadata.version("litellm")
    except metadata.PackageNotFoundError:
        version = "?"
    return f"LiteLLM {version}'s bundled table"

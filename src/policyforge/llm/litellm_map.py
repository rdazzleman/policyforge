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
import sys
from importlib import metadata

#: LiteLLM's names for a per-token price. Kept out of the table's literals:
#: bandit reads any "*token" key beside a number as a hardcoded password.
_IN, _OUT = "input_cost_per_token", "output_cost_per_token"


def _per_million(input_usd: float, output_usd: float, **info) -> dict:
    """A LiteLLM map entry from prices in dollars per million tokens, the unit
    OpenRouter lists them in, so each figure reads against its source as is."""
    return {_IN: input_usd / 1e6, _OUT: output_usd / 1e6, **info}


#: The provider's list figures for models PolicyForge names, where LiteLLM's map
#: has omitted a figure or had it lower. Keyed as LiteLLM keys them. Each entry
#: carries where its figures came from, the day they were read, and what the
#: bundled map said at that review (`was`). Its `litellm` value holds the LIST
#: figures (and, for a model the map lacks, the fields LiteLLM needs to route it).
#:
#: **The rule (80's ruling on #110), per price component:** the estimate is the
#: HIGHER of the map's figure and the provider's list price as read at the
#: review. An estimate below what the user will pay is the harmful direction,
#: so the map can't simply win. But a high the provider has since abandoned
#: (DeepSeek's) only makes the re-send notice noise.
#:
#: **Applied at load, against the map in use** (80's ruling on #244): `load()`
#: compares each list figure with the figure in `litellm.model_cost` after the
#: import, whichever map that is, and registers only where the list is higher
#: (`effective`). Applied once by hand against 1.103.0's map and frozen, the
#: rule became a price CUT on LiteLLM 1.104.0, whose map rose above our list
#: for glm's and kimi's output (b5 measured it on #244).
#:
#: Source: OpenRouter's public model list, https://openrouter.ai/api/v1/models
#: (`pricing.prompt`, `pricing.completion`, `top_provider.max_completion_tokens`),
#: re-read 2026-10-03 16:54 CDT by b5 on #110, sha256
#: a4590112de0ab775493a337df38b2fcd2563b26738e9141beebb8db6a4fb0eb4.
#: The bundled map is LiteLLM 1.103.0's.
#:
#: `varies` records every reading taken where the list price moved, so the next
#: reviewer sees the volatility, and the re-send notice says the figure is soft.
#: Deleted at that review, because the bundled figure is at or above the list on
#: every component, with their readings kept here:
#:   deepseek-v4-flash  $0.0763/$0.1526 (09-29 17:45), $0.14/$0.28 (09-29 19:38),
#:                      $0.042/$0.084 (10-02, 9b), $0.028/$0.056 (10-03 16:08, 1d;
#:                      and 16:54, b5); bundled $0.03724/$0.07448, 384,000 out.
#:   deepseek-v4-pro    $0.915936/$1.831872 (09-29 17:45), $0.95526/$1.91052 (09-29
#:                      19:38), $0.2088/$0.4176 (10-02, 9b; 10-03 16:08, 1d; 16:54,
#:                      b5); bundled $0.422298/$0.844596, 384,000 out.
#:
#: Reviewed at each minor release: re-read the source, apply the rule, and let
#: `tests/test_litellm_map.py` say whether each entry still corrects something.
OVERRIDES: dict[str, dict] = {
    "openrouter/anthropic/claude-opus-5.5": {
        "source": "OpenRouter model list",
        "as_of": "2026-10-03",
        "was": "absent (get_model_info fell back to Anthropic's entry: 64,000 out, no price)",
        "litellm": _per_million(
            4, 20, max_output_tokens=128000, litellm_provider="openrouter", mode="chat"
        ),
    },
    "openrouter/z-ai/glm-5.3-flash": {
        "source": "OpenRouter model list",
        "as_of": "2026-10-03",
        "was": "$0.09/M in, $0.30/M out; 131,072 out",
        "litellm": _per_million(0.15, 0.50, max_output_tokens=943717),
    },
    "openrouter/moonshotai/kimi-k3": {
        "source": "OpenRouter model list",
        "as_of": "2026-10-03",
        "was": "$1.70/M in, $8.50/M out; 943,718 out",
        "varies": (
            "$3/$15 (09-29); $2.70/$13.50 (10-02, 9b); $0.72/$13 (10-03 16:08 CDT, 1d); "
            "$0.639/$13 (10-03 16:54 CDT, b5); $0.499 in (10-03 19:46-19:47 CDT, 1d and ba "
            "on #185). Input stays bundled ($1.70, above the list)"
        ),
        # Output only: the list's $13/M is above the bundled $8.50/M; its input
        # ($0.639/M) is below the bundled $1.70/M, so the bundled figure stands.
        "litellm": {_OUT: 13 / 1e6},
    },
}

#: The components the rule takes the higher of. The output ceiling is one too:
#: a lower ceiling than the provider allows only makes a re-send ask for less.
_COMPONENTS = (_IN, _OUT, "max_output_tokens")


def effective(listed: dict, in_map: dict | None) -> dict:
    """What to register for one model: each list figure that is HIGHER than
    the map's for that component, or that the map lacks (80's rule on #110,
    applied to the map in use, #244). A field that isn't a component (the
    `litellm_provider` and `mode` a model outside the map needs) is kept only
    where the map has none. An empty result means the map's figures stand."""
    in_map = in_map or {}
    corrections = {}
    for field, value in listed.items():
        if field in _COMPONENTS:
            theirs = in_map.get(field)
            if theirs is None or value > theirs:
                corrections[field] = value
        elif field not in in_map:
            corrections[field] = value
    return corrections


#: None until `load()` has run; then {model: the fields it registered}. A model
#: whose list figures no longer beat the map registers nothing and isn't here.
_registered: dict | None = None

#: Which figures in a map are ours (#251). `register_model` writes into
#: `litellm.model_cost` in place, so after a first `load()` the map holds our
#: figures; a second `load()` (after `_registered` is reset) that compared the
#: list with it would find nothing to correct, and `basis()` would name
#: LiteLLM's table for a figure that is ours. So, for EVERY map object read
#: (not only the last: a stand-in map in between must not cost the real one
#: its record, 9b on PR 290), one entry `(map, first_seen, written)`: the
#: figures each `OVERRIDES` model had when that map was first read, and
#: exactly what `load()` wrote into it. Neither is overwritten afterwards.
#: Holding the map keeps its identity from being reused while recorded.
_records: list[tuple[dict, dict[str, dict], dict[str, dict]]] = []


def _record(table: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """`(first_seen, written)` for `table`, made on its first sight."""
    for seen, first, written in _records:
        if seen is table:
            return first, written
    first = {key: dict(table.get(key) or {}) for key in OVERRIDES}
    written: dict[str, dict] = {}
    _records.append((table, first, written))
    return first, written


def _map_figures(key: str, table: dict) -> dict:
    """The map's own figures for `key`: what `table` holds now, except a
    figure equal to the one `load()` wrote there, which is the map's as first
    seen (or none, if the map lacked it). A figure anyone else changed is the
    map's, as the rule on #244 requires: the map in memory, whichever won."""
    first_seen, written = _record(table)
    ours = written.get(key, {})
    first = first_seen.get(key, {})
    figures = {}
    for field, value in (table.get(key) or {}).items():
        if field in ours and value == ours[field]:
            if field in first:
                figures[field] = first[field]
        else:
            figures[field] = value
    return figures


def load():
    """LiteLLM, on its bundled map, with each `OVERRIDES` list figure
    registered where it's higher than the map's (`effective`).

    The rule reads `litellm.model_cost` AFTER the import: the map LiteLLM
    actually loaded, which is what `escalation._price` then reads. That is the
    bundled map whenever PolicyForge imports LiteLLM first, and whatever map an
    earlier importer loaded otherwise (80's condition on #244).

    Raises ImportError when LiteLLM isn't installed, exactly as `import
    litellm` would, so callers keep their own fallbacks.
    """
    global _registered
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import litellm

    if _registered is None:
        table = getattr(litellm, "model_cost", None)
        if table is None:
            table = {}
        # Noted before anything is registered into it, on this map's first
        # sight (#251).
        _, written = _record(table)
        corrections = {}
        for key, entry in OVERRIDES.items():
            fields = effective(entry["litellm"], _map_figures(key, table))
            if fields:
                corrections[key] = fields
        if corrections:
            # register_model warns, per entry, that a model outside the bundled
            # map has no cache pricing. That is true and not about this run.
            quiet = logging.getLogger("LiteLLM")
            level = quiet.level
            quiet.setLevel(logging.ERROR)
            try:
                litellm.register_model(corrections)
            finally:
                quiet.setLevel(level)
            for key, fields in corrections.items():
                written.setdefault(key, {}).update(fields)
        _registered = corrections
    return litellm


#: The components a price is built from, in the order `basis` names them.
_PARTS = (("input", _IN), ("output", _OUT), ("output ceiling", "max_output_tokens"))


def basis(key: str | None) -> str:
    """Where the map figures for `key` come from, in words a user can check.

    **Per component** (80's wording on #247): the price rule picks input,
    output and the output ceiling one by one (#110, at load since #244), so
    one model's figures can come from two places. A user checking a figure
    against the source we name must find it there. So:

    - every component from one source: that source, once;
    - mixed: each source with the components it holds, grouped, in the
      order input, output, output ceiling (a group placed by its first
      component), e.g. `input: PolicyForge's override table (…); output and
      output ceiling: LiteLLM 1.104.0's bundled table`.

    A component is ours exactly when `load()` registered it; any other the
    map has of its own (`_map_figures`: the map in memory, less what `load()`
    wrote into it, #251) is the map's. A component neither holds isn't named.
    """
    table = f"LiteLLM {_version()}'s bundled table"
    entry = OVERRIDES.get(key or "")
    ours = (_registered or {}).get(key or "", {}) if entry else {}
    if not ours:
        return table
    # A price that has moved between readings is soft; say so (80, #110).
    soft = "; this provider's price has varied" if entry.get("varies") else ""
    override = f"PolicyForge's override table ({entry['source']}, as of {entry['as_of']}{soft})"
    in_map = _map_figures(key, getattr(sys.modules.get("litellm"), "model_cost", None) or {})
    groups: dict[str, list[str]] = {}
    for name, field in _PARTS:
        if field in ours:
            groups.setdefault(override, []).append(name)
        elif field in in_map:
            groups.setdefault(table, []).append(name)
    if len(groups) <= 1:
        return next(iter(groups), override)
    # Mixed: at most two of the three share a source, so "a and b" suffices.
    return "; ".join(f"{' and '.join(names)}: {source}" for source, names in groups.items())


def _version() -> str:
    try:
        return metadata.version("litellm")
    except metadata.PackageNotFoundError:
        return "?"

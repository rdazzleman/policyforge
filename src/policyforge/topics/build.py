"""Build the registry `init` writes: the pool, the rule's selection, one file
(22, part 3).

The shipped `config/topics.example.yaml` is the **pool**: every topic the rule
can pick from, each anchored as it is when it is on its own. `select` says
which are on and which fold; this turns that into a registry:

* a **merge** moves the donor's anchors and evidence to the receiver, which
  keeps its owner and accountability and names the donor's owner as a
  contributor in its description;
* the **role** the `ai` fact declares sets the AI block's anchors: a user of
  AI has Responsible Use of AI hold the categories of the topics it replaces,
  and a builder whose two extras are kept has each take one category from the
  topic it came out of;
* a Policy no remaining topic names is left out.

The output is a plain registry, so everything that reads one reads it.
"""

from __future__ import annotations

import copy
import textwrap

import yaml

from policyforge.topics.convergence import AREAS, BAR, CEILING, UNVERIFIED
from policyforge.topics.rule import Merge, Selection
from policyforge.topics.scope_facts import ScopeFacts

#: (the extra that takes it, the topic it comes out of, the category). 80's
#: ruling R4 (6445): anchored at category level, since anchoring a category
#: claims its subcategories.
CARVES = (
    ("Data for AI Systems", "Third-Party AI & Data Supply Chain", "Map 4"),
    ("Transparency to Affected People", "AI System Inventory & Impact Assessment", "Map 5"),
)


class PoolError(ValueError):
    """The pool doesn't have a topic the rule names: the table and the file
    have drifted, which no organisation's facts can fix."""


def _by_name(pool: dict) -> dict[str, dict]:
    return {t["name"]: copy.deepcopy(t) for t in pool.get("topics") or []}


def _absorb(receiver: dict, donor: dict) -> None:
    for key in ("nist_controls", "evidence"):
        have = list(receiver.get(key) or [])
        for item in donor.get(key) or []:
            if item not in have:
                have.append(item)
        receiver[key] = have
    sentence = (
        f"Also covers {donor['name']}: {donor['owner']} contributes, and the owner of "
        "this topic stays accountable."
    )
    description = str(receiver.get("description") or "").strip()
    receiver["description"] = f"{description} {sentence}".strip()


def _apply_merge(topics: dict[str, dict], merge: Merge) -> None:
    if merge.into not in topics:
        raise PoolError(f"{merge.line()} but the pool has no topic {merge.into!r}.")
    for name in merge.donors:
        if name not in topics:
            raise PoolError(f"{merge.line()} but the pool has no topic {name!r}.")
        _absorb(topics[merge.into], topics[name])


def _carve(topics: dict[str, dict], kept: set[str]) -> None:
    for taker, parent, category in CARVES:
        if taker not in kept:
            continue
        if taker not in topics or parent not in topics:
            raise PoolError(f"the pool lacks {taker!r} or {parent!r}.")
        anchors = list(topics[parent].get("nist_controls") or [])
        if category not in anchors:
            raise PoolError(f"{parent!r} doesn't anchor {category!r} in the pool.")
        anchors.remove(category)
        topics[parent]["nist_controls"] = anchors
        topics[taker]["nist_controls"] = [category]


def load_pool() -> dict:
    """The shipped pool as a mapping: `config/topics.example.yaml` as packaged
    (or in the checkout), parsed. Raises RuntimeError where nothing is bundled."""
    from policyforge.scaffold import bundled_root

    text = bundled_root().joinpath("config", "topics.example.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def build_registry(pool: dict, selection: Selection) -> dict:
    """The registry mapping for `selection`, from the pool mapping."""
    topics = _by_name(pool)
    for merge in selection.merges:
        _apply_merge(topics, merge)
    _carve(topics, set(selection.topics))
    chosen = []
    for name in selection.topics:
        if name not in topics:
            raise PoolError(f"the rule selects {name!r}, which the pool doesn't have.")
        chosen.append(topics[name])
    used = {str(t.get("policy") or "").strip().casefold() for t in chosen}
    policies = [
        copy.deepcopy(p)
        for p in pool.get("policies") or []
        if str(p.get("name") or "").strip().casefold() in used
    ]
    result: dict = {"topics": chosen}
    if policies:
        result["policies"] = policies
    return result


def rests_on_unverified(selection: Selection) -> list[str]:
    """Areas that are on only because of a column not verified: take the
    unverified cells away and they fall under the bar."""
    names: list[str] = []
    for area in AREAS:
        if area.id not in selection.areas_on or area.condition:
            continue
        verified = sum(c.weight for f, c in area.cells.items() if f not in UNVERIFIED)
        if area.score >= BAR > verified:
            names.append(area.name)
    return names


def header(facts: ScopeFacts, selection: Selection) -> str:
    """The comment at the top of a written registry: what it was built from."""
    pairs = [f"{k}={str(v).lower()}" for k, v in facts.declared().items()]
    declared = ", ".join(pairs) or "none declared"
    lines = [
        "# Written by `policyforge init` from the scope facts under `programme:` in",
        "# config/config.yaml (22). Edit it freely: it is yours, and `init` never",
        "# overwrites it. To start again, delete it and run `policyforge init`.",
        "#",
        f"# Scope facts: {declared}.",
        f"# {selection.count} topics (the ceiling is {CEILING}).",
    ]
    if selection.undeclared:
        lines.append(
            "# Not declared, so treated as off: "
            + ", ".join(selection.undeclared)
            + ". Declare them to turn their topics on."
        )
    for merge in selection.merges:
        lines.append("# Folded: " + merge.line())
    if selection.dropped_extras:
        lines.append(
            "# Left out to stay at the ceiling: "
            + ", ".join(selection.dropped_extras)
            + ". Their categories stay with the topics they came out of."
        )
    shaky = rests_on_unverified(selection)
    if shaky:
        lines.append(
            "# Only on if the unverified columns of the table are counted "
            f"({', '.join(UNVERIFIED)}): " + ", ".join(shaky) + "."
        )
    lines.append("# Run `policyforge coverage` to see what the topics leave unowned.")
    wrapped: list[str] = []
    for line in lines:
        # Long lines wrap under their own `# ` so the header stays a comment.
        wrapped.extend(
            textwrap.wrap(line, width=92, subsequent_indent="#   ", break_long_words=False) or ["#"]
        )
    return "\n".join(wrapped) + "\n\n"


def render(pool: dict, facts: ScopeFacts, selection: Selection) -> str:
    """The text of `config/topics.yaml` for these facts."""
    body = yaml.safe_dump(
        build_registry(pool, selection),
        sort_keys=False,
        allow_unicode=True,
        width=96,
        default_flow_style=False,
    )
    return header(facts, selection) + body


__all__ = [
    "CARVES", "PoolError", "build_registry", "header", "load_pool", "render", "rests_on_unverified",
]  # fmt: skip

"""The topic rule: scope facts in, a topic set out (22, part 3; 80's 5925,
5927 and the rulings of comment 6445).

    1. **Core.** An area at least 3 of 5 reference structures name at top
       level, a partial match counting half (`convergence`), is a topic.
    2. **Conditional, from scope facts.** Builds software turns on Secure
       Development; own facilities, Physical; personal data, Privacy (and PHI
       its two HIPAA topics); an 800-53, FedRAMP or GovRAMP baseline keeps
       Personnel Security and System Maintenance.
    3. **Merge by default.** An area under the bar folds into its nearest
       core topic, even when another team does part of the work: the core
       topic's owner stays accountable. A conditional area under the bar
       (Personnel, Maintenance) folds the same way when its fact is off. A
       conditional area that meets the bar (Secure Development, Physical)
       is simply absent when its fact is off: there is nothing to fold.
    4. **AI is a block, sized by role.** `user`: 3 topics; `builder`: the
       example's 5, and up to 2 more.
    5. **The ceiling is 25**, AI block included. Over it, in this order and
       never touching a core area or the AI block's core: (1) the AI extras,
       then (2) the lowest-score non-core areas fold into their nearest core
       topic, ties going to the later table row. Every merge is named.

A pure function of the facts and the table, so the counts it produces are
checked against hand-written cases, not against itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from policyforge.topics.convergence import AI, AREAS, BAR, CEILING, Area
from policyforge.topics.scope_facts import ScopeFacts


@dataclass(frozen=True)
class Merge:
    """Topics folded into another: their anchors become its, accountability
    stays with it, and the donors' owners are named as contributors."""

    area: str
    donors: tuple[str, ...]
    into: str
    #: Why: "below the bar (2.0 of 3)" or "the ceiling (score 1.0)".
    why: str

    def line(self) -> str:
        names = ", ".join(self.donors)
        verb = "folds" if len(self.donors) == 1 else "fold"
        return f"{names} {verb} into {self.into}: {self.why}."


@dataclass
class Selection:
    #: The topics, in table order, the AI block last.
    topics: list[str] = field(default_factory=list)
    merges: list[Merge] = field(default_factory=list)
    #: AI extras that were left out to stay at or under the ceiling.
    dropped_extras: list[str] = field(default_factory=list)
    #: Facts nobody declared, treated as off.
    undeclared: list[str] = field(default_factory=list)
    #: Areas on, by id, for `coverage`.
    areas_on: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.topics)

    def receiver_of(self, donor: str) -> str | None:
        for merge in self.merges:
            if donor in merge.donors:
                return merge.into
        return None


def _on(area: Area, facts: ScopeFacts) -> bool:
    """An area is on when its fact says so, or the bar does."""
    if area.condition:
        return bool(getattr(facts, area.condition))
    return area.meets_bar


def select(facts: ScopeFacts) -> Selection:
    """The topics the rule gives for `facts`."""
    result = Selection(undeclared=facts.undeclared())

    kept: list[Area] = []
    for area in AREAS:
        if _on(area, facts):
            kept.append(area)
        elif not area.meets_bar and area.merge_into:
            # Below the bar and not on: folds into its nearest core topic.
            result.merges.append(
                Merge(
                    area.id,
                    area.topics,
                    area.merge_into,
                    f"{area.name} is below the bar ({area.score:g} of {BAR:g})",
                )
            )
        # else: a conditional area that meets the bar and is off is absent.

    ai: list[str] = []
    if facts.ai == "user":
        ai = list(AI.user)
        # A user of AI runs three AI topics, not five: the builder's other
        # three fold into Responsible Use of AI, which takes their categories.
        folded = tuple(t for t in AI.builder if t not in AI.user)
        result.merges.append(
            Merge(
                "ai_user",
                folded,
                "Responsible Use of AI",
                "ai: user runs three AI topics, and the AI RMF categories of the other "
                "three sit under the one that governs use",
            )
        )
    elif facts.ai == "builder":
        ai = list(AI.builder)
    extras = list(AI.extras) if facts.ai == "builder" else []

    def total() -> int:
        return sum(len(a.topics) for a in kept) + len(ai) + len(extras)

    # The ceiling, in the order 80 ruled (6445): extras, then the lowest-score
    # non-core areas, the later table row breaking a tie.
    dropped: list[str] = []
    while total() > CEILING and extras:
        dropped.append(extras.pop())
    row = {a.id: i for i, a in enumerate(AREAS)}
    while total() > CEILING:
        candidates = [a for a in kept if a.condition and a.merge_into]
        if not candidates:
            break
        victim = min(candidates, key=lambda a: (a.score, -row[a.id]))
        kept.remove(victim)
        result.merges.append(
            Merge(
                victim.id,
                victim.topics,
                victim.merge_into,
                f"the ceiling of {CEILING}: {victim.name} had the lowest score "
                f"({victim.score:g}) of the non-core areas",
            )
        )

    result.dropped_extras = list(reversed(dropped))
    result.areas_on = [a.id for a in kept]
    result.topics = [t for a in kept for t in a.topics] + ai + extras
    return result

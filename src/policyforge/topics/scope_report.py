"""What the registry leaves out of the scope the facts declare (22, part 3).

`coverage` measures controls: which in-scope controls no topic owns. This is
the question one level up: of the areas a security programme is expected to
have a topic for, which has none here, and is the registry past the ceiling.

**An area has a topic when any registry topic anchors a control the area's pool
topics anchor.** The footprint is read from the shipped pool, not from the
table's cell text, so a registry that renamed a topic or folded one into
another is still read by what it anchors. An area is expected when it meets the
bar and has no condition, or when its condition is on for the declared facts.
An area whose fact is declared off is not reported, and one whose fact nobody
declared is listed as undeclared, not as missing: the registry can't be faulted
for a topic a fact it was never told would have turned on.

Report only. It adds no exit code: `--strict` still means orphaned, contested
or unknown anchors, so a registry that passed a CI gate yesterday passes it
today.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from policyforge.topics.convergence import AREAS, BAR, CEILING, UNVERIFIED, Area
from policyforge.topics.registry import Topic
from policyforge.topics.scope_facts import ScopeFacts


@dataclass
class ScopeReport:
    topic_count: int = 0
    ceiling: int = CEILING
    #: Areas expected for the declared facts that no topic covers.
    missing: list[str] = field(default_factory=list)
    #: Of `missing`, those that are expected only if an unverified column of
    #: the table is counted (5921).
    on_unverified_columns: list[str] = field(default_factory=list)
    #: Facts nobody declared; each can turn an area on.
    undeclared: list[str] = field(default_factory=list)

    @property
    def over_ceiling(self) -> bool:
        return self.topic_count > self.ceiling


def _expected(area: Area, facts: ScopeFacts) -> bool:
    if area.condition:
        return bool(getattr(facts, area.condition))
    return area.meets_bar


def scope_report(topics: list[Topic], facts: ScopeFacts, pool: list[Topic]) -> ScopeReport:
    """The report for a registry's `topics` against `facts`, with `pool` the
    shipped topics the areas' footprints are read from."""
    owned = {anchor for topic in topics for anchor in topic.nist_controls}
    anchors_of = {topic.name: set(topic.nist_controls) for topic in pool}
    report = ScopeReport(topic_count=len(topics), undeclared=facts.undeclared())
    for area in AREAS:
        if not _expected(area, facts):
            continue
        footprint = set().union(*(anchors_of.get(name, set()) for name in area.topics))
        if footprint & owned:
            continue
        report.missing.append(area.name)
        verified = sum(c.weight for f, c in area.cells.items() if f not in UNVERIFIED)
        if not area.condition and verified < BAR:
            report.on_unverified_columns.append(area.name)
    return report


def format_scope(report: ScopeReport) -> list[str]:
    """Lines for a terminal, empty when there is nothing to say."""
    lines: list[str] = []
    if report.over_ceiling:
        lines.append(
            f"warning: {report.topic_count} topics, past the {report.ceiling} the starter "
            "registry is held to. More topics is more owners, pages and reviews to keep "
            "current; fold the smallest into the topic nearest it."
        )
    if report.missing:
        lines.append(
            "Areas the declared scope expects, with no topic anchoring any of their controls:"
        )
        for name in report.missing:
            note = (
                "  (only if the unverified table columns are counted)"
                if name in report.on_unverified_columns
                else ""
            )
            lines.append(f"  - {name}{note}")
    if report.undeclared:
        lines.append(
            "Scope facts not declared, so their topics are not expected: "
            + ", ".join(report.undeclared)
            + ". Declare them under `programme:` in config/config.yaml."
        )
    return lines


__all__ = ["ScopeReport", "format_scope", "scope_report"]

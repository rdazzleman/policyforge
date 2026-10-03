"""Paragraphs a court vacated that the catalog's source still prints (#409).

A catalog carries its source's text as the source prints it (a catalog
pinned to a revision is that revision, standing ruling). Where a court has
vacated part of that text and the source has not caught up, the catalog's
`framework.yaml` says so under `vacated:`, written by its ETL. Today only the
HIPAA Privacy Rule's does: eCFR still prints the 2024 reproductive-health
amendments that Purl v. HHS vacated.

Two kinds, by 80's ruling 1 on #409:

- **vacated**: text the rule added. Nothing binds in its place. Not used
  for generation, and `check` warns on a citation to it.
- **vacated-revision**: text the rule revised. The source prints the
  rule's wording, which is vacated; the pre-rule wording binds again. It
  is quoted in the manifest from the source's own earlier text, as an
  attributed annotation, and generation uses that wording instead. A
  citation to one is valid, and `check` does not warn on it (80, on #409),
  unless the id exists only because of the rule (`added_under_revision`,
  e.g. 164.502(g)(5)(i)(A)(1)), which `check` warns on, pointing at the
  root's pre-rule wording.

This is the project's reading of the judgment, not legal advice.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

VACATED = "vacated"
VACATED_REVISION = "vacated-revision"


@dataclass(frozen=True)
class Vacated:
    judgment: str
    added: frozenset[str]
    sections: frozenset[str]
    #: {id: the revised root it belongs to}
    revised: dict = field(default_factory=dict)
    #: {root: (scope, pre-rule wording)}
    pre_rule: dict = field(default_factory=dict)
    #: {id: root} for the revised ids the pre-rule text does not have at all
    #: (a subset of `revised`): the rule's own paragraphs under a revised root.
    added_under_revision: dict = field(default_factory=dict)

    def status(self, requirement_id: str) -> str | None:
        """`VACATED`, `VACATED_REVISION`, or None for text that binds as printed."""
        if requirement_id in self.added:
            return VACATED
        if any(requirement_id == s or requirement_id.startswith(s + "(") for s in self.sections):
            return VACATED
        if requirement_id in self.revised:
            return VACATED_REVISION
        return None


def _from_manifest(block: dict) -> Vacated:
    revised, pre_rule, added_under = {}, {}, {}
    for root, entry in (block.get("revised") or {}).items():
        pre_rule[root] = (str(entry.get("scope", "")), str(entry.get("pre_rule_text", "")))
        for rid in entry.get("ids") or ():
            revised[str(rid)] = root
        for rid in entry.get("added_ids") or ():
            added_under[str(rid)] = root
    return Vacated(
        judgment=str(block.get("judgment") or ""),
        added=frozenset(str(i) for i in block.get("added") or ()),
        sections=frozenset(str(i) for i in block.get("sections") or ()),
        revised=revised,
        pre_rule=pre_rule,
        added_under_revision=added_under,
    )


def declared_vacated(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> dict[str, Vacated]:
    """{framework key: its `vacated:` block}, for every catalog that has one."""
    from policyforge.frameworks.registry import _catalog_names, _read_manifest
    from policyforge.mapping.crosswalk import normalize_framework

    found: dict[str, Vacated] = {}
    for _, framework, names in _catalog_names(config, roots):
        block = _read_manifest(framework.path).get("vacated")
        if not isinstance(block, dict):
            continue
        status = _from_manifest(block)
        for key in {framework.key, *(normalize_framework(n) for n in names)} - {""}:
            found.setdefault(key, status)
    return found


_PRE_RULE_TITLE = "Pre-rule wording, binding again after Purl v. HHS"


def for_generation(control, status: Vacated):
    """`control` as generation may read it, or None if nothing of it binds.

    A copy: the loaded catalog keeps eCFR's text. Vacated paragraphs are
    dropped. A revised root's paragraphs are replaced by one entry carrying
    the pre-rule wording, under the root's id and marked as such; a revised
    statement is replaced by its pre-rule wording.
    """
    if status.status(control.control_id) == VACATED:
        return None
    view = copy.deepcopy(control)
    kept, placed = [], set()
    for enhancement in view.enhancements:
        eid = enhancement.enhancement_id
        kind = status.status(eid)
        if kind == VACATED:
            continue
        if kind == VACATED_REVISION:
            root = status.revised[eid]
            scope, wording = status.pre_rule[root]
            if root not in placed and scope in ("subtree", "self"):
                placed.add(root)
                enhancement.enhancement_id = root
                enhancement.title = _PRE_RULE_TITLE
                enhancement.description = wording
                kept.append(enhancement)
            continue
        kept.append(enhancement)
    view.enhancements = kept
    statement = status.pre_rule.get(control.control_id)
    if statement and statement[0] == "statement":
        view.control_statement = statement[1]
    if not view.enhancements and not view.control_statement:
        return None
    return view

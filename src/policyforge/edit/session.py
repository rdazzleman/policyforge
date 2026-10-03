"""One editing run over one or more Confluence pages.

`edit-confluence` edits a single page; `edit-topic` edits a topic's whole
Policy/Standard/Procedure set from one instruction. Both want the same
sequence — fetch, refuse anything unsafe, plan, rewrite, check — so it lives
here once rather than being written twice with the differences drifting.

The multi-page case is not three single-page runs in a loop. Two things make
it different:

* **The tiers absorb a change differently.** A cadence change belongs in the
  Standard and the Procedure and usually not in the Policy at all. Each
  planner call is told which tier it is looking at and that the siblings
  exist, so the same edit isn't pasted into all three at the wrong altitude.
* **Nothing publishes until everything is ready.** Every page is fetched,
  planned and rewritten before any of them is written back, so a failure
  part-way through leaves the whole set untouched rather than half-edited.
  Confluence has no cross-page transaction, so this narrows the window
  rather than closing it — a failure *during* the publish loop is reported
  page by page, naming exactly what landed and what didn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from policyforge.edit.apply import EditCheck, apply_edit_plan, check_edit, detect_unsupported_macros
from policyforge.edit.plan import EditPlan, build_edit_plan
from policyforge.llm.base import LLMProvider


@dataclass
class EditTarget:
    """One page in an editing run."""

    space: str
    title: str
    tier: str = ""
    #: Populated by `fetch_targets`.
    page_id: str = ""
    version: int = 0
    webui_url: str = ""
    original: str = ""
    unsupported_macros: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.title} ({self.tier})" if self.tier else self.title


@dataclass
class EditOutcome:
    """What planning and rewriting produced for one target."""

    target: EditTarget
    plan: EditPlan
    revised: str = ""
    check: EditCheck | None = None

    @property
    def has_changes(self) -> bool:
        return bool(self.revised) and not (self.check and self.check.unchanged)


def fetch_targets(targets: list[EditTarget], *, host: str) -> list[EditTarget]:
    """Fetch each target's current content and note any macros that wouldn't
    survive a round trip. Does not decide what to do about them — the caller
    owns that policy."""
    from policyforge.export.confluence_importer import confluence_to_markdown, fetch_confluence_page

    for target in targets:
        page = fetch_confluence_page(space=target.space, title=target.title, host=host)
        target.page_id = page.id
        target.title = page.title
        target.version = page.version
        target.webui_url = page.webui_url
        target.unsupported_macros = detect_unsupported_macros(page.storage_body)
        target.original = confluence_to_markdown(page.storage_body)
    return targets


def plan_targets(
    targets: list[EditTarget], instruction: str, provider: LLMProvider
) -> list[EditOutcome]:
    """Plan the instruction against every target, tier-aware."""
    outcomes: list[EditOutcome] = []
    for target in targets:
        siblings = [t.label for t in targets if t is not target]
        plan = build_edit_plan(
            instruction,
            target.original,
            provider,
            page_title=target.title,
            tier=target.tier,
            sibling_titles=siblings,
        )
        outcomes.append(EditOutcome(target=target, plan=plan))
    return outcomes


def apply_targets(outcomes: list[EditOutcome], provider: LLMProvider) -> list[EditOutcome]:
    """Rewrite every target whose plan has steps, and check each result.

    Targets whose plan came back empty are left alone — an instruction that
    legitimately doesn't touch the Policy should leave the Policy untouched,
    not force an edit into it.
    """
    for outcome in outcomes:
        if outcome.plan.is_empty:
            continue
        outcome.revised = apply_edit_plan(outcome.plan, outcome.target.original, provider)
        outcome.check = check_edit(outcome.target.original, outcome.revised, plan=outcome.plan)
    return outcomes

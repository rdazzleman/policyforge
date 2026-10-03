"""LLM-drafted control implementation narratives for a System Security Plan.

Division of labour, and why it matters here: the **control description** in
an SSP is NIST's own authoritative wording and is copied verbatim from the
800-53 catalog — never generated. What this module drafts is the
**implementation description**: the organization-specific narrative of *how*
a given control is satisfied, which is the part a human actually has to
write and the part an assessor reads.

That narrative is a starting draft, not an assertion of fact. Nothing here
can know what a system actually does, so the prompt is built to produce a
scaffold that follows the control's own structure and marks every unknown
with a `[Square-Bracket Placeholder]` — the same convention
`generate/policy_writer.py` uses — rather than inventing a plausible-sounding
control implementation. An SSP that confidently describes controls the
system doesn't have is worse than an empty one: it's a false attestation.
Every generated cell is therefore prefixed as a draft, and the workbook
carries a review column so unreviewed text is visibly unreviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from policyforge.generate.policy_writer import OrgContext
from policyforge.ingest.schema import Control
from policyforge.llm.base import LLMProvider

#: Prefix stamped on every generated narrative. Kept in one place because
#: `workbook.py` also uses it to flag unreviewed rows.
DRAFT_PREFIX = "[DRAFT — REVIEW REQUIRED]"


@dataclass
class SystemProfile:
    """The system an SSP describes, per NIST SP 800-18's plan elements.

    Everything is optional: an SSP is normally drafted incrementally, and a
    blank field should surface as a placeholder in the workbook for someone
    to fill in rather than block generation.
    """

    name: str = ""
    identifier: str = ""
    owner: str = ""
    authorizing_official: str = ""
    security_officer: str = ""
    operational_status: str = ""  # Operational | Under Development | Major Modification
    system_type: str = ""  # General Support System | Major Application | Minor Application
    confidentiality: str = ""  # FIPS 199: Low | Moderate | High
    integrity: str = ""
    availability: str = ""
    overall_categorization: str = ""
    description: str = ""
    environment: str = ""
    interconnections: str = ""
    laws_and_regulations: list[str] = field(default_factory=list)

    @property
    def baseline_hint(self) -> str:
        """The 800-53 baseline the categorization implies, if it's set."""
        return self.overall_categorization or ""


_SYSTEM_PROMPT = """You are drafting the "Implementation Description" cell of \
a NIST 800-53 System Security Plan (SSP) control table, for one \
organization's system.

You are producing a DRAFT SCAFFOLD for a human system owner to complete and
verify. You have no knowledge of what this system actually does.

Rules — the first is the most important:
- NEVER assert a specific implementation as fact unless it is stated in the
  system or organization context given to you. You do not know what tools,
  processes, frequencies, roles, or configurations exist. Where a detail is
  needed but unknown, write a `[Square-Bracket Placeholder]` naming what is
  needed (e.g. `[Identity Provider]`, `[Review Frequency]`,
  `[Responsible Team]`) instead of guessing a plausible value.
- Do not name real vendors or products unless they appear in the
  organization's vendor list provided below.
- Follow the control's own structure. If the control statement has lettered
  parts (a., b., c.), address each one in order, using the same letters, so
  the narrative can be checked part-by-part against the control. Assessors
  read it that way.
- Describe implementation, not intent: say what is done, by whom, and how
  often — in placeholder form where unknown. Do not restate or paraphrase
  the control text back as the answer; the control text is already in an
  adjacent column.
- Plain prose, present tense, third person ("The system...", "[Responsible
  Team] reviews..."). No markdown headings, no bullet characters, no code
  fences — this text goes into a single spreadsheet cell.
- Be concise: aim for 80-150 words unless the control has many parts.
"""


def _control_block(control: Control) -> str:
    lines = [f"Control: {control.control_id} — {control.title}"]
    if control.family:
        lines.append(f"Family: {control.family}")
    if control.baseline:
        lines.append(f"Baseline: {control.baseline}")
    lines.append("")
    lines.append("Control statement:")
    lines.append(control.control_statement or "(no statement text available)")
    if control.discussion:
        # Truncated: discussion is background for the drafter, and sending
        # every control's full guidance would dominate the prompt budget
        # without improving the narrative.
        lines.append("")
        lines.append(f"Supplemental guidance (context only): {control.discussion[:800]}")
    return "\n".join(lines)


def _context_block(org: OrgContext, system: SystemProfile) -> str:
    lines = ["Organization context:", f"- Name: {org.name or '[Organization Name]'}"]
    if org.industry:
        lines.append(f"- Industry: {org.industry}")
    lines.append(
        f"- Known vendors/tools: {', '.join(org.vendors) if org.vendors else '(none provided)'}"
    )
    lines.append("")
    lines.append("System context:")
    lines.append(f"- System name: {system.name or '[System Name]'}")
    for label, value in (
        ("Type", system.system_type),
        ("Operational status", system.operational_status),
        ("FIPS 199 categorization", system.overall_categorization),
        ("Description", system.description),
        ("Environment", system.environment),
        ("Interconnections", system.interconnections),
    ):
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def draft_implementation_narrative(
    control: Control,
    org: OrgContext,
    system: SystemProfile,
    provider: LLMProvider,
) -> str:
    """Draft one control's implementation description.

    Returns the narrative prefixed with `DRAFT_PREFIX`, so an unreviewed
    cell is self-identifying wherever it ends up.
    """
    stable, varying = _narrative_prompt(control, org, system)

    from policyforge.llm import effort

    response = effort.call(
        provider,
        effort=effort.NARRATIVE,
        cache=True,
        cache_prefix=stable,
        system=_SYSTEM_PROMPT,
        prompt=varying,
        max_tokens=NARRATIVE_TOKENS,
        temperature=NARRATIVE_TEMPERATURE,
    )
    return _finished(response.text)


#: One narrative is a cell in a spreadsheet, not an essay.
NARRATIVE_TOKENS = 900
NARRATIVE_TEMPERATURE = 0.1


def _narrative_prompt(control: Control, org: OrgContext, system: SystemProfile) -> tuple[str, str]:
    """The request, split where it stops repeating.

    This is the highest-volume path in the project — one call per control,
    several hundred in a run — and the organization block in front of each
    one is identical every time. Returned as two halves so the caller can
    mark the first as cacheable; the text sent is their concatenation, so
    what changes is the price and not the question.
    """
    stable = f"{_context_block(org, system)}\n\n"
    varying = (
        f"{_control_block(control)}\n\n"
        "Write the Implementation Description cell for this control now. "
        "Output the narrative text only — no preamble, no heading, no quotes."
    )
    return stable, varying


def _finished(text: str) -> str:
    """One narrative, or "" when the model said nothing worth a cell."""
    cleaned = " ".join(text.split())
    return f"{DRAFT_PREFIX} {cleaned}" if cleaned else ""


def draft_narratives(
    controls: list[Control],
    org: OrgContext,
    system: SystemProfile,
    provider: LLMProvider,
    *,
    progress=None,
    batch: bool = False,
) -> dict[str, str]:
    """Draft narratives for many controls, keyed by control ID.

    One request per control either way: grounding each call in a single
    control's text keeps the narrative attributable to that control, and
    that does not change. `progress` is called with each control ID as it
    starts, so a long run can report where it is.

    `batch` submits those requests together instead of one at a time, for
    half the price. Nobody watches a System Security Plan build, which is
    what makes this the right path for it — but it is opt-in, because a
    batch takes as long as the queue takes and a caller who wanted an answer
    now would be left waiting.
    """
    if batch:
        return _draft_as_batch(controls, org, system, provider)

    narratives: dict[str, str] = {}
    for control in controls:
        if progress is not None:
            progress(control.control_id)
        narratives[control.control_id] = draft_implementation_narrative(
            control, org, system, provider
        )
    return narratives


def _draft_as_batch(
    controls: list[Control],
    org: OrgContext,
    system: SystemProfile,
    provider: LLMProvider,
) -> dict[str, str]:
    """Every control in one submission, matched back by control ID.

    Refuses rather than falling back to one call at a time: somebody who
    asked for a batch asked for its price, and quietly spending twice that
    is not a smaller failure than stopping.
    """
    from policyforge.llm import effort
    from policyforge.llm.batch import BatchError, BatchRequest

    if not getattr(provider, "supports_batch", lambda: False)():
        raise BatchError(
            "The configured provider cannot submit a batch. Re-run without --batch "
            "to draft one control at a time, or configure the anthropic provider."
        )

    requests = []
    for control in controls:
        stable, varying = _narrative_prompt(control, org, system)
        requests.append(
            BatchRequest(
                custom_id=control.control_id,
                system=_SYSTEM_PROMPT,
                prompt=varying,
                max_tokens=NARRATIVE_TOKENS,
                temperature=NARRATIVE_TEMPERATURE,
                effort=effort.NARRATIVE,
                cache_prefix=stable,
            )
        )

    answers = provider.generate_batch(requests)
    # A batch cannot be retried one item at a time the way `effort.call`
    # retries a single reply, so a narrative that stopped at its budget is
    # refused here by name. The workbook is written after this returns, so
    # nothing partial reaches it.
    cut = sorted(cid for cid, answer in answers.items() if effort.truncated(answer))
    if cut:
        from policyforge.llm.base import TruncatedResponse

        raise TruncatedResponse(
            subject=f"ssp narratives for {', '.join(cut)}",
            site="ssp",
            first_budget=NARRATIVE_TOKENS,
            budget=NARRATIVE_TOKENS,
            stop_reason=getattr(answers[cut[0]], "stop_reason", None),
            model=getattr(answers[cut[0]], "model", None),
            text=getattr(answers[cut[0]], "text", "") or "",
        )
    # Keyed by the id that went out, never by position: results come back in
    # any order, and matching them by index would give one control's
    # narrative to another with nothing in the workbook to show it.
    return {
        control.control_id: _finished(answers[control.control_id].text)
        for control in controls
        if control.control_id in answers
    }

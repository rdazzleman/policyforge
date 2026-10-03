"""Turns a synthesized topic (see synthesis/merge.py) plus org context
(industry, vendor stack, existing docs) into a drafted document at one of
three tiers, matching this project's document hierarchy:

    Policy > Standard > Procedure

- **Standard** (`generate_standard`) is the detailed, technical tier:
  every synthesized requirement, source-tagged back to the frameworks it
  came from, vendor-specific where the org context allows it. Audience:
  security/IT staff who implement and audit against it.
- **Policy** (`generate_policy`) is the brief, principle-level tier read by
  the whole organization, not just practitioners. It compresses the same
  synthesized requirements into a small number of plain-language
  commitments, drops framework/control citations entirely (that
  traceability lives in the Standard), and points to the Standard by name
  for anyone who needs the specifics.
- **Procedure** (`generate_procedure`) is one level *more* granular than the
  Standard tier: it turns each requirement into the literal ordered steps a
  practitioner performs to satisfy it, still source-tagged for traceability.
  Audience: the same security/IT staff as the Standard, but read while
  actually doing the task rather than while auditing against it.

Output contract: every generator here must return portable, well-formed
CommonMark markdown — no Obsidian wikilinks, no vault-relative-only paths.
This is the *canonical* output; export/confluence_exporter.py converts this
same markdown to Confluence storage format rather than generating
Confluence content independently. See README's "Output format priority"
section — getting this contract right is what keeps both output formats
correct.

Documents get `[Square-Bracket Placeholder]` placeholders wherever a detail
isn't available from the organization context below (a vendor, an owning
team, an exceptions contact) rather than inventing a specific name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from policyforge.llm.base import LLMProvider
from policyforge.llm.prompts import Prompt, register


@dataclass
class OrgContext:
    name: str
    industry: str
    vendors: list[str] = field(default_factory=list)
    #: The role-keyed profile, when config supplied one. Present means the
    #: generator is told what each tool is *for* rather than left to infer
    #: it, and the placeholders it writes are role labels that a
    #: deterministic pass can fill in afterwards.
    profile: object | None = None


@dataclass
class TopicContext:
    """Which topic a document is being drafted for, and who owns it.

    Sourced from the topic registry (`topics/registry.py`) and carried into
    the generated document through the synthesis file's frontmatter. Its
    whole job is to remove one specific placeholder: without an owner, every
    generator writes `[Responsible Team]` wherever the document has to name
    who performs a step or answers for the outcome, which is precisely the
    ownership question the "one topic, one team" model exists to settle.
    """

    name: str = ""
    owner: str = ""
    cadence: str = ""
    evidence: list[str] = field(default_factory=list)
    #: NIST's suggested actions for the AI RMF subcategories this topic
    #: anchors, from `synthesis.merge.playbook_actions` via the synthesis
    #: frontmatter. **Read by `generate_standard` only** (#301): a Policy's
    #: sentences are the organization's commitments and a Procedure's are
    #: instructions, so a voluntary suggestion reaching either becomes one.
    playbook: list[dict] = field(default_factory=list)


#: The one sentence form a Standard uses for a Playbook subcategory (#301).
#: One constant, read by the prompt below and by the test that runs a sentence
#: in this form through the #309 gate, so the two cannot disagree again: the
#: first wording, "Among the N actions NIST suggests ...", began with "Among",
#: the gate reads the subject from the sentence start, and a measured run
#: flagged every compliant sentence (17 of 17).
PLAYBOOK_SENTENCE_FORM = "NIST suggests, among its N actions for <subcategory>, ..."

#: How several Playbook actions share one tag. **Every part names the
#: framework** (#301): the gate and the traceability reader split a merged
#: tag on "|" and read each part alone, so a part written as the shorthand
#: "Govern 1.1 Action 2" is not a Playbook citation to either of them. glm
#: wrote that shorthand in 17 of 17 sentences of one Standard when this rule
#: said only "separated by |", and the gate saw none of the 17.
PLAYBOOK_MERGED_TAG_EXAMPLE = (
    "[NIST AI RMF Playbook Govern 1.1 Action 1 | NIST AI RMF Playbook Govern 1.1 Action 2]"
)


_STANDARD_SYSTEM_PROMPT = register(
    Prompt(
        name="generate.standard",
        version=6,
        text=f"""You are a compliance policy drafting engine. \
Turn a set of already-synthesized, source-tagged requirement statements \
into a formal information security STANDARD document for one \
organization.

Rules:
- Output portable, well-formed CommonMark markdown only: a single '#'
  document title, '##' sections, no Obsidian-style [[wikilinks]], no
  vault-relative-only paths, properly closed code fences, well-formed
  tables. This markdown must render correctly unmodified on GitHub, in a
  plain text editor, or pasted into Confluence.
- Every requirement in the input must be reflected in the output — do not
  drop or water down a requirement, and do not add requirements that
  weren't in the input.
- Preserve each requirement's inline source tag (e.g. `[NIST 800-53 IA-5 |
  GovRAMP IA-5]`) so the document stays traceable back to the frameworks it
  was drawn from.
- A requirement tagged `[NIST AI RMF Playbook ...]` is NIST's voluntary
  suggestion, not a requirement. Write it as NIST suggesting the action
  ("NIST suggests ..."), keep its tag, and never give that sentence "must",
  "shall", "is required to" or any other obligation: this overrides the
  formal-language rule for these sentences only. If the organization adopts
  the action as its own requirement, say so in a SEPARATE sentence that does
  not carry the Playbook tag. NEVER put a Playbook tag on a heading, not even
  merged with another framework's tag: this overrides any rule that places
  tags on headings. Put it only on the "NIST suggests ..." sentence inside
  the section.
- If the input has a "NIST AI RMF Playbook" block, write exactly ONE
  sentence per subcategory in it, framed as NIST suggesting: \
"{PLAYBOOK_SENTENCE_FORM}" \
with N the count the block
  gives. Restate only what the actions you cite say, and cite every action
  you draw on with its tag. Several may share one tag, separated by "|",
  but EVERY part names the framework in full:
  `{PLAYBOOK_MERGED_TAG_EXAMPLE}`, never a
  part that starts at the subcategory.
  No Playbook action becomes a requirement of the organization.
- Where a requirement is vendor/tool-specific: if the tool list below fills
  that role, use that tool's actual name. If not, write the role itself in
  square brackets (`[Identity Provider]`, `[Ticketing System]`, `[Backup
  System]`) rather than naming a product the organization has not said it
  uses. Write the role exactly as it is worded in the list below where one
  applies — those labels are substituted automatically afterwards.
- Write in formal policy language ("must", "shall"), addressed to the
  security/IT staff who implement and audit against this document, not to
  a generic reader.
- Do not add a discretionary qualifier ("as appropriate", "where
  feasible", "as needed", "if possible", "should consider") that the input
  requirement does not contain: a requirement stated without conditions
  stays without conditions, because a qualifier turns it into something an
  auditor cannot test. Where the input requirement carries one — frameworks
  do, e.g. "establish (and implement as needed)" — keep it exactly.
  Dropping it states an obligation stricter than the rule being cited,
  which misstates it just as badly as weakening it.
- Never state a frequency, deadline, duration or count that is not given
  in the input requirements or in the organization context below — no
  "within 5 business days", "annually", "after 30 days" of your own. Where
  the document needs a value nobody has given you, write a square-bracket
  placeholder naming the missing decision (`[Review Frequency]`,
  `[Remediation Deadline]`), the same way an undecided `[Assignment: ...]`
  value stays undecided. An invented number reads exactly as authoritative
  as the requirement beside it, and nobody has agreed to it.
""",
    )
)

_POLICY_SYSTEM_PROMPT = register(
    Prompt(
        name="generate.policy",
        version=1,
        text="""You are a compliance policy drafting engine. \
Turn a set of already-synthesized, source-tagged requirement statements \
into a short, plain-language information security POLICY document for one \
organization. Unlike the Standard document these requirements also feed,
a Policy is read by the entire organization — most readers have no
compliance background and will never see a framework control ID.

Rules:
- Output portable, well-formed CommonMark markdown only: a single '#'
  document title, followed by these '##' sections in this exact order:
  Purpose, Scope, Policy Statements, Roles & Responsibilities, Exceptions,
  Related Standards, Enforcement. No Obsidian-style [[wikilinks]], no
  vault-relative-only paths.
- Purpose: 1-2 sentences, plain language, on why this policy exists.
- Scope: 1-2 sentences naming who/what it applies to.
- Policy Statements: this is the section you must compress the hardest.
  Merge the source requirements into a SHORT list of high-level,
  plain-language commitments — far fewer bullets than the number of source
  requirements you were given. Each bullet is one organizational
  commitment a non-technical employee could read once and remember, not a
  restatement of one technical control. Never cite a framework or control
  ID here. Do not shorten each source requirement 1:1 into its own bullet
  — genuinely merge related requirements together. If you find yourself
  writing more than a handful of bullets, you haven't compressed enough;
  go back and merge further.
- Roles & Responsibilities: 1-2 sentences on who owns and enforces this
  policy.
- Exceptions: 1-2 sentences on how someone requests an exception.
- Related Standards: name the Standard document given below by its title —
  this is where a reader goes for the technical specifics, and it's the
  only place framework/control traceability needs to live from here on.
- Enforcement: one sentence on the consequence of a violation.
- Never invent a specific fact (a named team, a named contact, a specific
  consequence) that isn't given to you below or in the source requirements.
  Where a detail is missing, use a square-bracket placeholder in the same
  style as vendor placeholders, e.g. `[Security Team]` or
  `[Policy Owner Title]`, rather than making one up.
""",
    )
)


_PROCEDURE_SYSTEM_PROMPT = register(
    Prompt(
        name="generate.procedure",
        version=3,
        text="""You are a compliance policy drafting engine. \
Turn a set of already-synthesized, source-tagged requirement statements \
into a formal information security PROCEDURE document for one \
organization — the step-by-step operational instructions for executing an \
existing Standard's requirements. This is one level MORE granular than the \
Standard, not a summary of it.

Rules:
- Output portable, well-formed CommonMark markdown only: a single '#'
  document title, followed by these '##' sections in this exact order:
  Purpose, Scope, Prerequisites, Procedure Steps, Roles & Responsibilities,
  Related Standard. No Obsidian-style [[wikilinks]], no vault-relative-only
  paths, properly closed code fences, well-formed tables.
- Purpose: 1-2 sentences on what executing this procedure accomplishes.
- Scope: 1-2 sentences naming who performs it and on what systems.
- Prerequisites: access, tools, or approvals needed before starting, as a
  bullet list. Use a square-bracket placeholder (e.g. `[Access Request
  System]`) for anything not given below rather than inventing one.
- Procedure Steps: one '###' subsection per input requirement (or tightly
  related group of requirements), each containing a numbered list of
  concrete, ordered actions — who does what, in what order, using which
  tool/system — that satisfy that requirement. Every requirement in the
  input must be reflected here; do not drop, water down, or merge unrelated
  requirements together the way the Policy tier does. Preserve each
  requirement's inline source tag (e.g. `[NIST 800-53 IA-5 | GovRAMP IA-5]`) at the
  end of its subsection heading or its first step, so the document stays
  traceable back to the frameworks it was drawn from.
- A requirement tagged `[NIST AI RMF Playbook ...]` is NIST's voluntary
  suggestion, not a requirement. Write it as NIST suggesting the action
  ("NIST suggests ..."), keep its tag, and never give that sentence "must",
  "shall", "is required to" or any other obligation: this overrides the
  formal-language rule for these sentences only. If the organization adopts
  the action as its own requirement, say so in a SEPARATE sentence that does
  not carry the Playbook tag. NEVER put a Playbook tag on a heading, not even
  merged with another framework's tag: this overrides any rule that places
  tags on headings. Put it only on the "NIST suggests ..." sentence inside
  the section.
- Steps invite deadlines. Never state a frequency, deadline, duration or count that is not given
  in the input requirements or in the organization context below — no
  "within 5 business days", "annually", "after 30 days" of your own. Where
  the document needs a value nobody has given you, write a square-bracket
  placeholder naming the missing decision (`[Review Frequency]`,
  `[Remediation Deadline]`), the same way an undecided `[Assignment: ...]`
  value stays undecided. An invented number reads exactly as authoritative
  as the requirement beside it, and nobody has agreed to it.
- Where a step is vendor/tool-specific: if the organization's vendor list
  below fills that role, use that tool's actual name and its real UI/CLI
  actions where you can reasonably infer them. If not, write the role itself
  in square brackets (`[Identity Provider]`, `[Ticketing System]`) rather
  than naming a real product or inventing specific UI steps for it.
- Roles & Responsibilities: 1-2 sentences on who is authorized to perform
  these steps and who reviews/approves exceptions.
- Related Standard: name the Standard document given below by its title —
  that document is where the *why* and the full requirement text live.
- Write in direct, imperative operational language ("Open the console and
  verify...", "Set the value to..."), addressed to the practitioner
  executing the procedure, not to an auditor.
""",
    )
)


def _render_org(org: OrgContext) -> str:
    """The organization block, from the role-keyed profile where there is one.

    The legacy branch is what a flat `vendors:` list has always produced, and
    it stays because config files in the wild use it.
    """
    if org.profile is not None:
        from policyforge.org.context import render_for_prompt

        return render_for_prompt(org.profile)

    lines = [f"Organization: {org.name}", f"Industry: {org.industry}"]
    if org.vendors:
        lines.append(f"Known vendors/tools: {', '.join(org.vendors)}")
    else:
        lines.append(
            "Known vendors/tools: none supplied — write the role in square "
            "brackets ([Identity Provider], [Ticketing System]) wherever the "
            "document needs to name a system."
        )
    return "\n".join(lines)


def _render_playbook(topic: TopicContext | None) -> str:
    """The Standard's Playbook block, or "" when the topic has none.

    Never part of `_render_context`, which every tier shares: leaving the
    Playbook out of the Policy and the Procedure is done by never giving it
    to them, not by removing it afterwards (80's ruling on #301).
    """
    if topic is None or not topic.playbook:
        return ""
    lines = [
        "NIST AI RMF Playbook -- NIST's VOLUNTARY suggested actions for this topic's "
        "AI RMF subcategories. These are suggestions, not requirements.",
        "",
    ]
    for entry in topic.playbook:
        actions = entry.get("actions") or []
        lines.append(f"Subcategory {entry.get('subcategory')} ({len(actions)} actions):")
        for action in actions:
            lines.append(f"- {action.get('text')} [NIST AI RMF Playbook {action.get('id')}]")
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_context(org: OrgContext, topic: TopicContext | None) -> str:
    """Org context, plus topic ownership when the registry supplied it."""
    block = _render_org(org)
    if topic is None or not topic.owner:
        return block

    lines = [block, ""]
    if topic.name:
        lines.append(f"Topic: {topic.name}")
    lines.append(
        f"Owning team: {topic.owner} — this team is accountable for this process end "
        "to end. Name it wherever the document must say who performs a step, who "
        "reviews, or who answers for the outcome. Do not write [Responsible Team], "
        "[Owning Team] or similar placeholders for that role; you have the answer. "
        "Other teams may appear as participants in individual steps, but "
        f"{topic.owner} owns the process and its handoffs."
    )
    if topic.cadence:
        lines.append(
            f"Cadence: {topic.cadence} — use this where the document states how often "
            "the process runs, instead of a placeholder frequency."
        )
    if topic.evidence:
        lines.append(
            "Evidence this process is expected to produce: "
            + "; ".join(topic.evidence)
            + ". Reference these artifacts where the document describes what is "
            "recorded or retained."
        )
    return "\n".join(lines)


def extract_title(markdown_text: str) -> str:
    """Pull the document title out of a generated document's leading '# '
    heading — used to have a Policy reference its Standard by name without
    requiring the caller to retype it."""
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    raise ValueError("No '# ' title heading found in markdown_text.")


def generate_standard(
    topic_synthesis: str,
    org: OrgContext,
    provider: LLMProvider,
    *,
    topic: TopicContext | None = None,
    org_actors: tuple[str, ...] = (),
) -> str:
    """Draft a Standard. For an AI topic, its Playbook sentences are checked
    and repaired before it is returned, or `PlaybookRepairFailed` is raised
    (#301): a Standard `policyforge check` would reject is not handed back.
    `org_actors` are the organization's names, as `check` reads them."""
    if not topic_synthesis.strip():
        raise ValueError("topic_synthesis is empty — nothing to draft a document from.")

    playbook = _render_playbook(topic)
    prompt = (
        f"{_render_context(org, topic)}\n\n"
        f"Synthesized requirements:\n\n{topic_synthesis}\n\n"
        + (f"{playbook}\n\n" if playbook else "")
        + "Draft the Standard document per the rules above."
    )
    from policyforge.llm import effort

    response = effort.call(
        provider,
        effort=effort.DRAFTING,
        system=_STANDARD_SYSTEM_PROMPT,
        prompt=prompt,
        temperature=0.2,
        max_tokens=LONG_DOCUMENT_TOKENS,
    )
    document = effort.document_text(response, what="Standard")
    if topic is not None and topic.playbook:
        from policyforge.generate.playbook_repair import repair_standard

        actors = tuple(dict.fromkeys(a for a in (org.name, *org_actors) if a))
        document = repair_standard(document, topic.playbook, provider, actors)
    return document


#: A Standard or a Procedure. From the first 20-topic cost run
#: (glm-5.3-flash, 2026-09-17): at 8192, Standards ran a median of 4,149
#: output tokens with p90 8,088 and two of twenty cut off at the ceiling;
#: Procedures a median of 4,065, p90 7,966, two of twenty cut off. A p90
#: at the budget means one document in ten was about to be truncated, so
#: the budget is doubled; `llm/effort.py` retries once at twice this.
#:
#: Doubled again to 32768 by #3, measured uncensored (the first budget at
#: 32768, the 8x re-send off; MEASUREMENTS.md epoch 25). Standards for five
#: topics needed at most 9,110 tokens on glm, 25,386 on sonnet-5 and 24,070
#: on opus-5.5, so 3 of 5 sonnet and 2 of 5 opus Standards were cut off at
#: 16384 and paid for twice. Across 25 topics at 16384 with the retry, every
#: opus Procedure's first attempt was cut off (5 of 5), and one glm Standard
#: needed 27,666. Every uncensored final measured fits under 32768, except
#: deepseek's reasoning runaways, which no budget fixes. n = 5 per model at
#: 32k, plus those 25 topics: re-check if the prompts or topics change. The
#: budget is a ceiling, not a charge: a model is billed for what it writes.
#: Clamped to a model's published ceiling where one is known (`llm/ceiling.py`).
LONG_DOCUMENT_TOKENS = 32768
#: A Policy compresses the Standard rather than enumerating it: same run,
#: median 582, maximum 1,104, none cut off.
POLICY_TOKENS = 4096


def generate_policy(
    topic_synthesis: str,
    org: OrgContext,
    provider: LLMProvider,
    *,
    standard_title: str,
    topic: TopicContext | None = None,
) -> str:
    if not topic_synthesis.strip():
        raise ValueError("topic_synthesis is empty — nothing to draft a document from.")
    if not standard_title.strip():
        raise ValueError(
            "standard_title is required so the Policy's Related Standards "
            "section can name what it points to."
        )

    prompt = (
        f"{_render_context(org, topic)}\n\n"
        f"This policy's implementing Standard document is titled: "
        f"{standard_title!r}\n\n"
        "Synthesized requirements (the Standard above is built from these "
        "in full detail; you must compress them, not enumerate them):\n\n"
        f"{topic_synthesis}\n\n"
        "Draft the Policy document per the rules above."
    )
    from policyforge.llm import effort

    response = effort.call(
        provider,
        effort=effort.DRAFTING,
        system=_POLICY_SYSTEM_PROMPT,
        prompt=prompt,
        temperature=0.2,
        max_tokens=POLICY_TOKENS,
    )
    return effort.document_text(response, what="Policy")


def generate_procedure(
    topic_synthesis: str,
    org: OrgContext,
    provider: LLMProvider,
    *,
    standard_title: str,
    topic: TopicContext | None = None,
) -> str:
    if not topic_synthesis.strip():
        raise ValueError("topic_synthesis is empty — nothing to draft a document from.")
    if not standard_title.strip():
        raise ValueError(
            "standard_title is required so the Procedure's Related Standard "
            "section can name what it operationalizes."
        )

    prompt = (
        f"{_render_context(org, topic)}\n\n"
        f"This procedure operationalizes the Standard document titled: "
        f"{standard_title!r}\n\n"
        "Synthesized requirements (turn each into ordered, concrete steps "
        "per the rules above):\n\n"
        f"{topic_synthesis}\n\n"
        "Draft the Procedure document per the rules above."
    )
    from policyforge.llm import effort

    response = effort.call(
        provider,
        effort=effort.DRAFTING,
        system=_PROCEDURE_SYSTEM_PROMPT,
        prompt=prompt,
        temperature=0.2,
        max_tokens=LONG_DOCUMENT_TOKENS,
    )
    return effort.document_text(response, what="Procedure")

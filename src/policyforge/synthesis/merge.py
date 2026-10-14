"""Topic-themed merge/dedupe engine.

Reproduces the pattern already proven out manually in the source Obsidian
vault's Synthesis/ folder — for a given real-world topic (e.g. "Password &
Credential Management"), pull every control/element that maps to it across
the enabled frameworks, dedupe overlapping requirements, and produce a
single prose statement per requirement with inline source tags, e.g.:

    "Passwords must be a minimum of 14 characters for privileged accounts.
    [NIST 800-53 IA-5 | GovRAMP Moderate]"

This is the highest-value, most novel piece of the pipeline — it's what
turns a pile of controls into something a policy can actually be written
from. Uses an LLMProvider (see llm/base.py) for the actual merge/rewrite
step, with the source elements passed in as grounding context (not relying
on the model's own knowledge of framework text).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from policyforge.ingest.schema import Control
from policyforge.llm.base import LLMProvider
from policyforge.llm.prompts import Prompt, register
from policyforge.mapping.crosswalk import TOPIC_ANCHORS, normalize_framework


@dataclass
class SynthesisTopic:
    name: str
    controls: list[Control]
    #: {"<framework> <id>": requirements it resolved to}, for each id this
    #: topic looked up that more than one distinct requirement carries
    #: (80's ruling on #187). All of them are in `controls`; this names the
    #: fact so the run can say it rather than merge them silently.
    shared_ids: dict[str, int] = field(default_factory=dict)
    #: {"<framework> <requirement id>": why}, for each level requirement the
    #: crosswalk named that the declared level scope leaves out (#416). Not
    #: sent; named so the run can say so.
    scoped_out: dict[str, str] = field(default_factory=dict)
    #: "<framework> <id>" for each crosswalk id that names a LOADED catalog
    #: and resolves to nothing in it (#416). An id for a catalog that isn't
    #: loaded is expected to resolve to nothing and is not listed.
    unresolved: list[str] = field(default_factory=list)
    #: Frameworks whose level statements this topic sends with no
    #: `frameworks.scoping` declared for them, so every level the crosswalk
    #: named went (80's ruling on #416: the run says so).
    unscoped: list[str] = field(default_factory=list)
    #: {reference id: levels the scope left out}, for each reference reached
    #: WHOLE (by its own id) that lost levels to the scope. Summarised in one
    #: line rather than named one by one (80's ruling on PR 419), so a
    #: whole reference's levels don't bury the output.
    whole_scoped_out: dict[str, int] = field(default_factory=dict)


#: Frontmatter keys `synthesize` writes and `generate` reads back. The
#: synthesis file is the hand-off between two separate commands, so the topic's
#: ownership has to travel *in* the file rather than being re-supplied on the
#: second command line — otherwise the owning team is known when the topic is
#: assembled and forgotten by the time the document is drafted.
#:
#: `content_class` and `derived_from` travel for the same reason and matter
#: more. A synthesis drawn from a HITRUST export is a restatement of HITRUST
#: requirement text, but once written it is an ordinary file under `output/`,
#: and `classify_path` reads an ordinary file as the organization's own. The
#: class `synthesize` worked out was dropped at the hand-off, and `generate`
#: sent licensed-derived text to whatever provider was configured.
SYNTHESIS_FRONTMATTER_KEYS = (
    "topic",
    "owner",
    "policy",
    "cadence",
    "evidence",
    "nist_controls",
    "content_class",
    "derived_from",
)


def write_synthesis(
    body: str,
    *,
    topic: str,
    owner: str = "",
    cadence: str = "",
    evidence: list[str] | None = None,
    nist_controls: list[str] | None = None,
    content_class: str | None = None,
    derived_from: list[str] | None = None,
    catalogs: list[dict] | None = None,
    playbook: list[dict] | None = None,
    policy: str = "",
) -> str:
    """Render a synthesis file: YAML frontmatter, then the requirement list.

    Only keys with a value are emitted, so a call with none of them writes
    the requirement list alone. `synthesize` always passes a content class,
    so every synthesis it writes says what it was drawn from.

    `playbook` goes in the frontmatter and never in the body (#301): the body
    is shared by all three tiers, and only the Standard may draw on NIST's
    voluntary suggestions. Kept out of the text every tier reads, so no
    tier's input has to be edited to leave it out.
    """
    import yaml

    metadata = {
        "topic": topic,
        "owner": owner,
        "policy": policy,
        "cadence": cadence,
        "evidence": evidence or [],
        "nist_controls": nist_controls or [],
        "content_class": content_class or "",
        "derived_from": derived_from or [],
        # Each catalog's hash and library version (443 part 2), carried into
        # every document `generate` drafts from this file.
        "catalogs": catalogs or [],
        "playbook": playbook or [],
    }
    metadata = {k: v for k, v in metadata.items() if v}
    if not metadata:
        return body.rstrip() + "\n"
    front = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body.rstrip()}\n"


#: The AI RMF Core, which topics anchor, and the Playbook, whose actions are
#: keyed by the Core's subcategory ids. As `normalize_framework` keys them.
_AI_RMF_CORE = "nist-ai-rmf"
_PLAYBOOK = "nist-ai-rmf-playbook"


def playbook_actions(anchor_ids: list[str], controls: list[Control]) -> list[dict]:
    """NIST's suggested actions for the AI RMF subcategories a topic anchors.

    **Derived, not listed** (#301, 80's ruling). The Core states outcomes;
    NIST put the actions in the Playbook, keyed by the same subcategory ids,
    so the link is NIST's and no selection is made here. A category anchor
    ("Govern 1") covers each of its subcategories; a subcategory anchor
    ("Govern 1.1") covers only itself.

    **The Playbook is deliberately NOT in `TOPIC_ANCHORS`.** A topic does not
    own NIST's voluntary suggestions, so `/coverage` must not count them as
    things an organization can fail to cover. This is retrieval for the
    Standard tier only.

    Returns one entry per subcategory, in anchor order:
    `{"subcategory": "Govern 1.1", "actions": [{"id": ..., "text": ...}]}`.
    Empty when either catalog is not loaded.
    """
    core = {}
    for control in controls:
        if normalize_framework(control.framework) == _AI_RMF_CORE:
            core[control.control_id] = control
            for enhancement in control.enhancements:
                core.setdefault(enhancement.enhancement_id, control)
    playbook = {c.control_id: c for c in controls if normalize_framework(c.framework) == _PLAYBOOK}
    if not core or not playbook:
        return []

    entries: list[dict] = []
    seen: set[str] = set()
    for anchor_id in anchor_ids:
        control = core.get(anchor_id)
        if control is None:
            continue
        anchored = (
            [e.enhancement_id for e in control.enhancements]
            if control.control_id == anchor_id
            else [anchor_id]
        )
        for subcategory in anchored:
            entry = playbook.get(subcategory)
            if entry is None or subcategory in seen:
                continue
            seen.add(subcategory)
            entries.append(
                {
                    "subcategory": subcategory,
                    "actions": [
                        {"id": a.enhancement_id, "text": " ".join(a.description.split())}
                        for a in entry.enhancements
                    ],
                }
            )
    return entries


def read_synthesis(text: str) -> tuple[dict, str]:
    """Split a synthesis file into `(metadata, body)`.

    A file with no frontmatter yields `({}, text)` — synthesis files written
    before this existed still load.
    """

    from policyforge.textfile import loads_frontmatter

    parsed = loads_frontmatter(text)
    return dict(parsed.metadata), parsed.content


_SYSTEM_PROMPT = register(
    Prompt(
        name="synthesis.merge",
        # 1 because this is the first REGISTERED version, not the first text.
        # The prompt predates the registry and git shows three earlier
        # revisions; the fingerprint identifies the text, version only
        # orders. Calling it 2 would invite a search for a v1 that was
        # never recorded.
        version=1,
        text="""You are a compliance content synthesis engine. Merge \
overlapping control requirements from multiple frameworks, for one topic, \
into a deduplicated set of plain-English requirement statements.

Rules:
- Base every statement ONLY on the control text provided below — never rely
  on your own knowledge of what a framework "usually" requires. If the
  provided text doesn't specify a detail (e.g. a minimum length or a
  frequency), do not invent one.
- Merge requirements that say the same thing across frameworks into one
  statement rather than repeating it once per framework.
- Where a "Framework-defined parameter value" is given, use it in place of
  the matching `[Assignment: ...]` or `[Selection: ...]` placeholder in the
  control text. That value is the framework's own decision, not a guess —
  it is the one case where filling in a placeholder is correct. Leave any
  placeholder with no such value as it stands.
- Treat "Additional framework requirements" as normative: they are
  requirements that framework adds on top of the base control, so they earn
  their own statement or extend an existing one.
- Where frameworks genuinely disagree (e.g. different minimums), keep them
  as separate statements rather than silently picking one.
- Output a markdown bullet list, one requirement per bullet, and nothing
  else — no preamble, no closing remarks.
- End every bullet with an inline source tag listing every framework/control
  it was drawn from, e.g. `[NIST 800-53 IA-5 | GovRAMP IA-5]`. Name the
  framework as its catalog declares it — `NIST 800-53`, not `NIST` — so a
  citation still resolves when a second NIST-family catalog is loaded.
  Include the baseline in the tag when the source control specifies one,
  e.g. `[GovRAMP IA-5 Moderate]`.
""",
    )
)


def _render_profile_additions(item, prefix: str = "") -> list[str]:
    """The two things a profile (GovRAMP, FedRAMP) adds to a base control.

    Worth handing to the model rather than dropping, because both change
    what the merged requirement should say. A profile that has already
    decided "at least every 3 years" turns `[Assignment:
    organization-defined frequency]` from a placeholder the model would
    otherwise fill in by guessing into a value with a citation behind it —
    which is the same reason `parameters/ledger.py` exists. The added
    requirements are normative sentences that appear in no base catalog, so
    a synthesis drawn only from 800-53 would silently omit them.
    """
    lines = []
    if item.parameter_values:
        values = "; ".join(
            f"{citation} = {value}" for citation, value in item.parameter_values.items()
        )
        lines.append(f"{prefix}Framework-defined parameter values: {values}")
    if item.additional_requirements:
        flattened = " ".join(item.additional_requirements.split())
        lines.append(f"{prefix}Additional framework requirements: {flattened}")
    return lines


def _render_control(control: Control) -> str:
    lines = [f"### {control.framework} {control.control_id} — {control.title}"]
    if control.baseline:
        lines.append(f"Baseline: {control.baseline}")
    if control.control_statement:
        lines.append(f"Control statement: {control.control_statement}")
    if control.discussion:
        lines.append(f"Discussion: {control.discussion}")
    lines.extend(_render_profile_additions(control))
    for enh in control.enhancements:
        lines.append(
            f"Enhancement {enh.enhancement_id} ({enh.baseline}) — {enh.title}: {enh.description}"
        )
        lines.extend(_render_profile_additions(enh, prefix="  "))
    # Level statements (HITRUST's), the sentences an assessor grades. Before
    # #416 none were rendered, so a level-scoped control sent its heading
    # alone. `build_synthesis_topic` narrows `requirements` to the levels
    # the topic reached and the scope counts before this runs.
    for requirement in control.requirements:
        lines.append(f"Requirement {requirement.requirement_id}: {requirement.statement}")
    return "\n".join(lines)


def synthesis_prompt(topic: SynthesisTopic) -> str:
    """The request `synthesize_topic` sends for `topic`, built once, so the
    estimate a spend ceiling plans from (#125) is the text that is sent."""
    source_text = "\n\n".join(_render_control(c) for c in topic.controls)
    return (
        f"Topic: {topic.name}\n\n"
        f"Source controls:\n\n{source_text}\n\n"
        "Produce the merged requirement list per the rules above."
    )


def synthesize_topic(topic: SynthesisTopic, provider: LLMProvider) -> str:
    if not topic.controls:
        raise ValueError(f"SynthesisTopic {topic.name!r} has no controls to synthesize.")

    prompt = synthesis_prompt(topic)
    from policyforge.llm import effort

    # Only the system prompt repeats here — every topic brings different
    # controls — so the breakpoint sits at the end of it. Worth marking
    # because a run synthesizes a topic set, not one topic.
    response = effort.call(
        provider,
        effort=effort.SYNTHESIS,
        cache=True,
        system=_SYSTEM_PROMPT,
        prompt=prompt,
        temperature=0.1,
        max_tokens=SYNTHESIS_TOKENS,
    )
    return effort.document_text(response, what="synthesis")


#: The output budget for one topic's merged requirement list.
#:
#: Sized from the first 20-topic cost run (glm-5.3-flash via OpenRouter,
#: 2026-09-17, the 20 starter topics over NIST, FedRAMP, ARC-AMPE and
#: HIPAA). With no budget named, the 4096 default applied and 11 of 20
#: syntheses stopped at it and were written as finished. The nine that
#: completed used a median of 4096 and up to 13,569 output tokens, p90
#: 12,170 — a reasoning model spends part of the budget thinking before
#: it writes, and the ledger counts both. So a topic that finishes needs
#: room for about 14k, and `llm/effort.py` retries a cut-off reply once at
#: twice this before refusing. Measured, and to be re-measured when the
#: cost table is re-run on this budget.
SYNTHESIS_TOKENS = 16384


def build_synthesis_topic(
    name: str,
    nist_control_ids: list[str],
    controls: list[Control],
    crosswalk: dict[str, dict[str, list[str]]],
    vacated: dict | None = None,
    scopes: dict | None = None,
) -> SynthesisTopic:
    """Assemble a SynthesisTopic by pulling the given NIST controls plus,
    via `crosswalk` (see mapping/crosswalk.py's `build_crosswalk`), every
    other framework's control that maps to them. `controls` is the pool to
    pull from — typically every loaded control across all enabled
    frameworks.

    A crosswalk entry may name either a control or one of its enhancements,
    since some frameworks are mapped at the sub-requirement level (NIST's
    HIPAA-to-800-53 crosswalk maps most of its rows to individual
    Required/Addressable implementation specifications rather than to the
    parent Standard). An enhancement ID resolves to the control that carries
    it, so those mappings pull their surrounding requirement into the topic
    instead of silently matching nothing.

    **Text a court vacated is never a source** (80's ruling on #409). A
    catalog whose manifest marks paragraphs `vacated:` is read through
    `frameworks.vacated.for_generation`: vacated paragraphs are dropped, and
    a revised one is replaced by the pre-rule wording that binds again.
    `vacated` defaults to the manifests on disk.

    **Two requirements can share an id, and both are kept** (80's ruling on
    #187). A real HITRUST export has several such ids, and `/coverage` counts
    each as its own requirement, so keying one control per id dropped all
    but the last from the document while coverage still claimed it. Now:

    - every control that carries an id as its OWN id is pulled in;
    - an exact repeat (same framework, same id, and the same text as the
      prompt would show it, `_render_control`) is one requirement written
      twice, and is pulled in once;
    - an enhancement id still resolves to the control carrying it only when
      no control has that id as its own (today's precedence, unchanged), and
      to the first such carrier;
    - each id that resolved to more than one requirement is named in
      `SynthesisTopic.shared_ids`, and `synthesize` prints it.

    **A crosswalk id may name a LEVEL requirement** (#416). HITRUST maps per
    level statement, so the crosswalk names `11.c Level 2`, an id carried by
    `Control.requirements` and by no control or enhancement. Before #416
    it resolved to nothing and HITRUST never reached synthesis, while
    coverage counted it reached. Now it resolves to its control, narrowed
    to the level statements the topic named (a control named by its own id
    keeps all of them). Precedence: a control's own id, then an
    enhancement id, then a level id.

    **Levels follow the declared scope, as coverage counts them** (80's
    ruling on #416). A level `frameworks.scoping` leaves out is not sent,
    and is named in `SynthesisTopic.scoped_out` with coverage's reason.
    With no scope declared for a framework, every named level is sent and
    the framework is named in `SynthesisTopic.unscoped`. `scopes` defaults
    to the config on disk.

    **An id that resolves to nothing is named, never dropped** (#416): in
    `SynthesisTopic.unresolved`, for each catalog that is loaded.
    """
    if vacated is None:
        from policyforge.frameworks.registry import config_or_defaults
        from policyforge.frameworks.vacated import declared_vacated

        vacated = declared_vacated(config_or_defaults("vacated paragraphs were read"))
    if scopes is None:
        from policyforge.frameworks.registry import config_or_defaults
        from policyforge.topics.coverage import level_scopes

        scopes = level_scopes(config_or_defaults("level scoping was read"))
    from policyforge.topics.coverage import scoped_out_levels

    left_out = scoped_out_levels(controls, scopes)
    by_framework_id: dict[tuple[str, str], list[Control]] = {}
    rendered: dict[tuple[str, str], set[str]] = {}
    for control in controls:
        key = (normalize_framework(control.framework), control.control_id)
        text = _render_control(control)
        if text not in rendered.setdefault(key, set()):
            rendered[key].add(text)
            by_framework_id.setdefault(key, []).append(control)
    for control in controls:
        framework = normalize_framework(control.framework)
        for enhancement in control.enhancements:
            by_framework_id.setdefault((framework, enhancement.enhancement_id), [control])
    # Level ids last, so they never shadow a control or enhancement id.
    by_level: dict[tuple[str, str], Control] = {}
    for control in controls:
        framework = normalize_framework(control.framework)
        for requirement in control.requirements:
            by_level.setdefault((framework, requirement.requirement_id), control)
    # Each loaded catalog's own name by its key, so an unresolved id is
    # spelt as a scoped-out one is (9b on PR 419).
    loaded = {normalize_framework(c.framework): c.framework for c in controls}

    # Controls in the order the topic reached them, each with the level ids
    # it was reached by, or None when it was reached whole.
    order: list[Control] = []
    reached_by: dict[int, set[str] | None] = {}
    shared_ids: dict[str, int] = {}
    scoped_out: dict[str, str] = {}
    unresolved: list[str] = []

    def _resolve(key: tuple[str, str]) -> list[Control]:
        found = by_framework_id.get(key, [])
        if len(found) > 1:
            shared_ids[f"{found[0].framework} {key[1]}"] = len(found)
        return found

    def _add(control: Control, level: str | None = None) -> None:
        if id(control) not in reached_by:
            order.append(control)
            reached_by[id(control)] = None if level is None else {level}
        elif level is None:
            reached_by[id(control)] = None
        elif reached_by[id(control)] is not None:
            reached_by[id(control)].add(level)

    def _crosswalked(key: tuple[str, str]) -> None:
        found = _resolve(key)
        for control in found:
            _add(control)
        if found:
            return
        carrier = by_level.get(key)
        if carrier is None:
            label = f"{loaded.get(key[0], key[0])} {key[1]}"
            if key[0] in loaded and label not in unresolved:
                unresolved.append(label)
        elif key in left_out:
            scoped_out[f"{carrier.framework} {key[1]}"] = left_out[key]
        else:
            _add(carrier, key[1])

    for nist_id in nist_control_ids:
        # **Both meanings of "anchor" meet on these two lines, and they are
        # not the same meaning.**
        #
        # Resolving a topic's own anchor is a TOPIC-anchor question -- which
        # catalogs may a topic claim ids from -- so it looks in every
        # anchorable catalog. This line read `NIST_ANCHOR` until
        # 2026-09-20, which meant a topic anchoring `Govern 1` resolved
        # NOTHING: every AI topic pulled zero controls while `/coverage`
        # reported those same topics owning 91 requirements. Two views of
        # one registry, disagreeing, and only one of them user-facing.
        #
        # Expanding through the crosswalk below is a CROSSWALK-anchor
        # question -- what a requirement is mapped ONTO -- and stays keyed
        # on `NIST_ANCHOR` by construction. At most one anchorable catalog
        # holds any given id, so the loop cannot double-count -- true of the
        # catalogs that anchor today, and held rather than assumed:
        # `test_anchor_decisions.py` fails if a catalog is made anchorable
        # that shares an id with another (#183, folded into #175).
        #
        # policyforge-80 found this: the A/B split that separated the two
        # constants was assigned per FILE, and this file does both.
        for anchor in TOPIC_ANCHORS:
            for control in _resolve((anchor, nist_id)):
                _add(control)
        for framework, equivalent_ids in crosswalk.get(nist_id, {}).items():
            for equivalent_id in equivalent_ids:
                _crosswalked((framework, equivalent_id))

    topic_controls: list[Control] = []
    unscoped: list[str] = []
    whole_scoped_out: dict[str, int] = {}
    for control in order:
        framework = normalize_framework(control.framework)
        if control.requirements:
            if framework not in scopes and control.framework not in unscoped:
                unscoped.append(control.framework)
            levels = reached_by[id(control)]
            kept = [
                r
                for r in control.requirements
                if (levels is None or r.requirement_id in levels)
                and (framework, r.requirement_id) not in left_out
            ]
            if levels is None:
                dropped = sum(
                    1 for r in control.requirements if (framework, r.requirement_id) in left_out
                )
                if dropped:
                    whole_scoped_out[control.control_id] = (
                        whole_scoped_out.get(control.control_id, 0) + dropped
                    )
            if len(kept) != len(control.requirements):
                control = replace(control, requirements=kept)
        status = vacated.get(framework)
        if status is not None:
            from policyforge.frameworks.vacated import for_generation

            control = for_generation(control, status)
        if control is not None:
            topic_controls.append(control)

    return SynthesisTopic(
        name=name,
        controls=topic_controls,
        shared_ids=shared_ids,
        scoped_out=scoped_out,
        unresolved=unresolved,
        unscoped=unscoped,
        whole_scoped_out=whole_scoped_out,
    )

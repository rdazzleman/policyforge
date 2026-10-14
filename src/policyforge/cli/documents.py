"""Drafting with a model: synthesis, System Security Plans, policy documents."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import (
    _DEFAULT_HISTORY_DIR,
    _content_dir,
    budgeted,
    controls_option,
    get_provider,
    load_catalogs,
    load_config,
    max_cost_option,
    plan_spend,
    resolve_catalogs,
)
from policyforge.content.provenance import UNEDITED, edit_state
from policyforge.org.context import load_org_profile
from policyforge.textfile import write_text_lf


def _enforce_catalogs(paths, config, *, hint: str = ""):
    """Refuse any catalog that may not leave for the configured provider.

    Returns each path's classification, in order, for the caller to record.

    `synthesize` and `ssp` both send control text to a model and each carried
    its own copy of this check. The copies had already drifted once in a way
    that mattered: both computed the content class as "the first catalog's",
    so 800-53 listed ahead of an organization-internal catalog labelled the
    whole set public domain, and the fix had to be made twice. One helper
    means the next fix is made once.

    `hint` is what the two did differently on purpose. `ssp` has a zero-call
    way to finish — `--no-narratives` builds the workbook without a model —
    and names it in the refusal; `synthesize` has none and says nothing more.
    Checking every catalog before classifying any is also preserved: a refusal
    names the first catalog that cannot leave, before any work is done.
    """
    from policyforge.llm.boundary import (
        UNMARKED_WARNING,
        BoundaryViolation,
        classify_path,
        enforce,
        unmarked_licensed_catalog,
    )

    for path in paths:
        try:
            enforce(path, config)
        except BoundaryViolation as exc:
            raise click.ClickException(f"{exc}\n  {hint}" if hint else str(exc)) from exc
    # A catalog a licensed ETL wrote before #459 (b) carries no marker, and
    # saved outside local_content/ it classifies as the organization's own.
    # Said, not refused: the shape is a heuristic (80, on #459).
    for path in paths:
        if unmarked_licensed_catalog(Path(path), config):
            click.echo(f"Warning: {path}: {UNMARKED_WARNING}", err=True)
    return [classify_path(path, config) for path in paths]


def _most_restrictive(classified) -> str | None:
    """The most guarded content class among `classified`, or None if empty.

    Most restrictive, not first-named. The order in `CONTENT_CLASSES` runs
    from least to most guarded, so a synthesis drawn from any licensed
    catalog is licensed-derived however the catalogs were listed.
    """
    from policyforge.llm.boundary import CONTENT_CLASSES

    if not classified:
        return None
    return max((c.klass for c in classified), key=CONTENT_CLASSES.index)


def _current_crosswalk(crosswalk_path: Path) -> dict:
    """crosswalk.json, refused when it wasn't built from the overlays now in
    config/crosswalks/.

    The catalogs carry the organization's crosswalk overlays, but
    crosswalk.json is whatever `map` wrote last. Built from different
    overlays — before one was added, edited or deleted — it would pull a
    rejected pair's requirement into the synthesis or miss a restored one,
    so it is refused rather than used. Compared by content: a modification
    time can tie, and a deleted overlay has none.
    """
    import json

    from policyforge.crosswalk.overlay import overlay_digests, provenance_path

    current = overlay_digests()
    record = provenance_path(crosswalk_path)
    built_from = (
        json.loads(record.read_text(encoding="utf-8")).get("overlays", {})
        if record.exists()
        else None
    )
    if (built_from is None and current) or (built_from is not None and built_from != current):
        raise click.ClickException(
            f"{crosswalk_path} was not built from the crosswalk overlays now in "
            "config/crosswalks/, so it may hold pairs they reject or lack pairs they "
            "accept. Rebuild it with `policyforge map` first."
        )
    return json.loads(crosswalk_path.read_text(encoding="utf-8"))


@cli.command("synthesize")
@click.option(
    "--topic-name",
    default=None,
    help="Name of a topic in the registry (config/topics.yaml). Takes its anchor "
    "controls and owning team from there, and records the owner in the output so "
    "`generate` names the real team instead of [Responsible Team].",
)
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(path_type=Path),
    help="Topic registry to resolve --topic-name against.",
)
@click.option(
    "--topic",
    default=None,
    help="Ad-hoc topic name, when not using the registry. Requires --nist-controls.",
)
@click.option(
    "--nist-controls",
    default=None,
    help="Comma-separated NIST control IDs anchoring an ad-hoc topic, e.g. IA-5,IA-5(1). "
    "Ignored when --topic-name is given.",
)
@controls_option("Path to a controls.json (one per loaded framework). Repeatable.")
@click.option(
    "--crosswalk",
    "crosswalk_path",
    default=Path("data/frameworks/crosswalk.json"),
    type=click.Path(exists=True, path_type=Path),
    help="Path to the crosswalk.json produced by `policyforge map`.",
)
@click.option(
    "--parameters",
    "parameters_path",
    default=Path("config/parameters.yaml"),
    type=click.Path(path_type=Path),
    help="Ledger of decided organization-defined parameter values. Missing is "
    "fine - undecided parameters stay as [Assignment: ...] placeholders.",
)
@click.option(
    "--out-dir",
    default=Path("output/synthesis"),
    type=click.Path(path_type=Path),
    help="Directory to write the synthesized topic markdown into.",
)
@max_cost_option
def synthesize_cmd(
    topic_name: str | None,
    topics_path: Path,
    topic: str | None,
    nist_controls: str | None,
    controls_paths,
    crosswalk_path: Path,
    parameters_path: Path,
    out_dir: Path,
    max_cost: float | None,
):
    """Merge/dedupe controls for one topic into synthesized requirement prose.

    Either `--topic-name <registry topic>`, which takes the anchor controls and
    the owning team from config/topics.yaml, or `--topic` plus
    `--nist-controls` for a one-off topic that isn't in the registry.
    """
    import re

    from policyforge.synthesis.merge import (
        build_synthesis_topic,
        playbook_actions,
        synthesize_topic,
        write_synthesis,
    )
    from policyforge.topics.registry import load_topics

    if topic_name and (topic or nist_controls):
        raise click.UsageError(
            "--topic-name takes its name and anchors from the registry; don't also "
            "pass --topic/--nist-controls."
        )

    owner = cadence = policy = ""
    evidence: list[str] = []
    if topic_name:
        registry = load_topics(topics_path)
        match = next((t for t in registry if t.name.lower() == topic_name.lower()), None)
        if match is None:
            raise click.UsageError(
                f"No topic named {topic_name!r} in {topics_path}. Available: "
                + ", ".join(sorted(t.name for t in registry))
            )
        topic = match.name
        nist_ids = list(match.nist_controls)
        owner, cadence, evidence = match.owner, match.cadence, list(match.evidence)
        # The parent Policy travels in the file too (22, part 2): `generate`
        # is a separate command and writes it into the Standard.
        policy = match.policy
    elif topic and nist_controls:
        # The same rule as the topic registry's (#188): `ac-3` is `AC-3`.
        from policyforge.topics.anchoring import canonical_anchor

        nist_ids = [canonical_anchor(c.strip()) for c in nist_controls.split(",") if c.strip()]
    else:
        raise click.UsageError(
            "Give either --topic-name (a topic from config/topics.yaml) or both "
            "--topic and --nist-controls."
        )

    config = budgeted(load_config(), max_cost)

    # Synthesis sends control statements to a model, so every catalog named
    # here is content leaving for the provider. A repository holding a
    # licensed HITRUST export under `local_content/` — which this project
    # tells people to do — can pass it to `--controls` as easily as it can
    # pass 800-53, and until this check the two were indistinguishable.
    controls_paths = resolve_catalogs(controls_paths)
    classified = _enforce_catalogs(controls_paths, config)

    controls = load_catalogs(controls_paths)

    # Decided parameter values go in before synthesis, not after. A
    # requirement that already says "quarterly" is one the model restates;
    # one that says [Assignment: organization-defined frequency] is one it
    # quietly decides, differently in every document.
    from policyforge.parameters.ledger import apply_to_controls, load_ledger

    decisions = load_ledger(parameters_path)
    if decisions:
        controls, filled = apply_to_controls(controls, decisions)
        click.echo(f"Applied {len(decisions)} recorded parameter decision(s): {filled} filled.")

    crosswalk = _current_crosswalk(crosswalk_path)
    from policyforge.topics.coverage import ScopingError

    try:
        synthesis_topic = build_synthesis_topic(topic, nist_ids, controls, crosswalk)
    except ScopingError as exc:
        raise click.ClickException(str(exc)) from exc
    if synthesis_topic.unresolved:
        # Said, never dropped (#416): an id the crosswalk names in a loaded
        # catalog that nothing there carries is a requirement this topic
        # claims and the synthesis would not see.
        click.echo(
            f"Crosswalk ids for topic {topic!r} that no loaded catalog carries, so not "
            f"synthesized: {', '.join(synthesis_topic.unresolved)}",
            err=True,
        )
    if synthesis_topic.scoped_out:
        click.echo(
            f"Level requirements for topic {topic!r} left out by frameworks.scoping "
            f"as coverage counts them: "
            + "; ".join(f"{i} ({why})" for i, why in synthesis_topic.scoped_out.items())
        )
    if synthesis_topic.whole_scoped_out:
        whole = synthesis_topic.whole_scoped_out
        levels, references = sum(whole.values()), len(whole)
        click.echo(
            f"{levels} further level{'s' if levels != 1 else ''} outside the declared scope "
            f"{'were' if levels != 1 else 'was'} left out of {references} whole "
            f"reference{'s' if references != 1 else ''} ({', '.join(whole)})"
        )
    if synthesis_topic.unscoped:
        click.echo(
            f"No level scoping is declared for {', '.join(synthesis_topic.unscoped)}, so "
            "every level the crosswalk names is synthesized. Declare yours under "
            "frameworks.scoping in config."
        )
    if not synthesis_topic.controls:
        click.echo(f"No controls found for topic {topic!r} — check its anchors and --controls.")
        raise SystemExit(1)
    if synthesis_topic.shared_ids:
        # Said, not merged silently (80's ruling on #187): a shared id is two
        # requirements, and both go to the synthesis.
        named = ", ".join(f"{i} ({n})" for i, n in synthesis_topic.shared_ids.items())
        click.echo(
            f"Ids in topic {topic!r} that more than one requirement carries, "
            f"each kept as its own requirement: {named}"
        )

    # One request, planned against a spend ceiling before it is made (#125).
    from policyforge.llm import spend
    from policyforge.synthesis.merge import _SYSTEM_PROMPT as SYNTHESIS_SYSTEM
    from policyforge.synthesis.merge import SYNTHESIS_TOKENS, synthesis_prompt

    plan = plan_spend(
        config,
        calls=1,
        input_tokens=spend.input_tokens_of(SYNTHESIS_SYSTEM, synthesis_prompt(synthesis_topic)),
        max_tokens=SYNTHESIS_TOKENS,
    )
    if plan:
        click.echo(plan)

    provider = get_provider(config)

    from policyforge.llm import ledger

    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")

    # The most restrictive content class among the catalogs that fed this
    # topic. A synthesis drawn from a licensed catalog is licensed-derived,
    # and the ledger should say so about the call rather than leaving it to
    # be re-derived later from paths that may have moved.
    content_class = _most_restrictive(classified)
    # Named by framework where the catalog declares one, by file otherwise,
    # so a refusal in `generate` can say which input made the text licensed.
    derived_from = [
        c.framework_id or path.name for c, path in zip(classified, controls_paths, strict=True)
    ]
    from policyforge.library import catalog_trace

    catalogs = [
        catalog_trace(Path(path), name)
        for name, path in zip(derived_from, controls_paths, strict=True)
    ]

    from policyforge.mapping.crosswalk import normalize_framework

    playbook = playbook_actions(nist_ids, controls)
    # Anchors that name an AI RMF Core category or subcategory the loaded
    # catalogs hold: the case where an empty Playbook block means "not loaded".
    core_ids = {
        i
        for c in controls
        if normalize_framework(c.framework) == "nist-ai-rmf"
        for i in (c.control_id, *(e.enhancement_id for e in c.enhancements))
    }
    ai_anchored = [i for i in nist_ids if i in core_ids]

    with (
        ledger.run(report=click.echo, planned=1),  # #379: what the run cost, said once
        ledger.about(f"synthesis/{slug}", site="synthesize", content_class=content_class),
    ):
        result = synthesize_topic(synthesis_topic, provider)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}.md"
    write_text_lf(
        out_path,
        write_synthesis(
            result,
            topic=topic,
            owner=owner,
            cadence=cadence,
            evidence=evidence,
            nist_controls=nist_ids,
            # Travels in the file, because `generate` is a separate command
            # and classify_path will read this file as the organization's own.
            content_class=content_class,
            derived_from=derived_from,
            catalogs=catalogs,
            # NIST's suggested actions for the AI RMF subcategories this topic
            # anchors, in the frontmatter so only the Standard reads them (#301).
            playbook=playbook,
            policy=policy,
        ),
    )
    click.echo(f"Synthesized {len(synthesis_topic.controls)} controls for {topic!r} -> {out_path}")
    # Said, not silent (1d on #359): an empty Playbook block reads the same
    # whether the topic has no AI RMF anchors or the catalog was not loaded.
    if playbook:
        actions = sum(len(entry["actions"]) for entry in playbook)
        click.echo(
            f"  NIST AI RMF Playbook: {len(playbook)} subcategories, {actions} suggested "
            "actions for the Standard"
        )
    elif ai_anchored and not any(
        normalize_framework(c.framework) == "nist-ai-rmf-playbook" for c in controls
    ):
        click.echo(
            f"  Warning: {len(ai_anchored)} AI RMF anchor(s) resolved, but no NIST AI RMF "
            "Playbook catalog is loaded, so the Standard gets none of NIST's suggested "
            "actions. Add --controls data/frameworks/nist-ai-rmf-playbook/controls.json."
        )
    if owner:
        click.echo(f"  Owner: {owner}" + (f" | cadence: {cadence}" if cadence else ""))
    else:
        click.echo(
            "  No owning team recorded — generated documents will use "
            "[Responsible Team]. Use --topic-name to pull the owner from the registry."
        )


@cli.command("ssp")
@controls_option(
    "Path to a controls.json. Repeatable - pass the NIST 800-53 file plus any "
    "other framework whose mappings you want shown as crosswalk columns."
)
@click.option(
    "--baseline",
    type=click.Choice(["low", "moderate", "high"], case_sensitive=False),
    default=None,
    help="Limit the plan to one NIST baseline. Controls and enhancements are "
    "selected independently, matching NIST's own profiles. Default: every control.",
)
@click.option(
    "--system-name", default=None, help="System name (overrides config.yaml's system.name)."
)
@click.option(
    "--narratives/--no-narratives",
    default=True,
    help="Draft implementation descriptions with the configured LLM (one request per "
    "control). --no-narratives builds the workbook with those cells left empty.",
)
@click.option(
    "--batch",
    is_flag=True,
    help="Submit the narratives as one batch, at half the price, instead of one "
    "request at a time. The run then waits for the queue rather than for the "
    "model, so use it when nobody is watching. Anthropic provider only.",
)
@click.option(
    "--yes", is_flag=True, help="Skip the confirmation prompt before making LLM requests."
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to write the workbook (default: output/ssp/<system-slug>-ssp.xlsx).",
)
@max_cost_option
def ssp_cmd(
    controls_paths,
    baseline: str | None,
    system_name: str | None,
    narratives: bool,
    batch: bool,
    yes: bool,
    out: Path | None,
    max_cost: float | None,
):
    """Build a NIST 800-53 System Security Plan as a spreadsheet workbook.

    Writes .xlsx - an open ISO standard that LibreOffice Calc opens and edits
    natively, so no Excel licence is needed. Control text is copied verbatim
    from the catalog; only the implementation narratives are LLM-drafted, and
    those are marked as drafts requiring review. See ssp/workbook.py.
    """
    import datetime
    import re
    from dataclasses import fields as dataclasses_fields

    from policyforge.generate.policy_writer import OrgContext
    from policyforge.mapping.crosswalk import TOPIC_ANCHORS, anchors_a_topic, build_crosswalk
    from policyforge.ssp.narrative import SystemProfile, draft_implementation_narrative
    from policyforge.ssp.workbook import build_ssp_workbook, select_for_baseline

    config = budgeted(load_config(), max_cost)
    org_cfg = config.get("org", {})
    profile = load_org_profile(config)
    org = OrgContext(
        name=org_cfg.get("name", ""),
        industry=org_cfg.get("industry", ""),
        vendors=profile.unkeyed_vendors,
        profile=profile,
    )
    if profile.unknown:
        click.echo(
            "WARNING: config names roles this version does not know: "
            + ", ".join(profile.unknown)
            + " — run `policyforge roles` for the list. They were ignored."
        )
    system_cfg = dict(config.get("system", {}) or {})
    if system_name:
        system_cfg["name"] = system_name
    known = {f.name for f in dataclasses_fields(SystemProfile)}
    unknown = sorted(set(system_cfg) - known)
    if unknown:
        raise click.UsageError(
            f"Unknown key(s) under `system:` in config.yaml: {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(known))}."
        )
    system = SystemProfile(**system_cfg)

    # Checked before the catalogs are read, like `synthesize`. This path was
    # missed on the grounds that it works from synthesis output, which is
    # simply wrong: `ssp` takes `--controls` and drafts one narrative per
    # control from the control text itself. It is also the highest-volume
    # model path in the project, so an unguarded run against a licensed
    # GovRAMP or HITRUST catalog would send several hundred requests of it
    # rather than one.
    controls_paths = resolve_catalogs(controls_paths)
    content_class = None
    if narratives:
        content_class = _most_restrictive(
            _enforce_catalogs(
                controls_paths,
                config,
                hint=(
                    "Or pass --no-narratives, which builds the workbook with no model calls at all."
                ),
            )
        )

    all_controls = load_catalogs(controls_paths)
    crosswalk = build_crosswalk(all_controls)

    # B-site: which catalogs a topic may anchor. Not the crosswalk anchor.
    nist_controls = [c for c in all_controls if anchors_a_topic(c.framework)]
    if not nist_controls:
        raise click.UsageError(
            "None of the --controls files contain a catalog a topic can anchor. "
            f"Topics anchor identifiers from: {', '.join(sorted(TOPIC_ANCHORS))}. "
            "Populate one first -- `policyforge etl-oscal` for 800-53."
        )

    scoped = select_for_baseline(nist_controls, baseline) if baseline else nist_controls
    if not scoped:
        raise click.UsageError(
            f"No controls matched the {baseline!r} baseline. Was the catalog loaded with "
            "baseline profiles (see `policyforge etl-oscal`)?"
        )

    drafted = {}
    if narratives:
        # Planned against a spend ceiling before anyone is asked to continue
        # (#125): one request per control, each priced at its own prompt and
        # at the narrative token limit.
        from policyforge.llm import spend
        from policyforge.ssp.narrative import (
            _SYSTEM_PROMPT as NARRATIVE_SYSTEM,
        )
        from policyforge.ssp.narrative import NARRATIVE_TOKENS, _narrative_prompt

        plan = plan_spend(
            config,
            calls=len(scoped),
            input_tokens=sum(
                spend.input_tokens_of(NARRATIVE_SYSTEM, *_narrative_prompt(c, org, system))
                for c in scoped
            ),
            max_tokens=NARRATIVE_TOKENS,
        )
        click.echo(
            f"Drafting implementation narratives for {len(scoped)} controls — "
            f"that is {len(scoped)} requests to your configured LLM provider."
            + (" Submitted together as one batch, at half the price." if batch else "")
        )
        if plan:
            click.echo(plan)
        if not yes:
            click.confirm("Continue?", abort=True)
        provider = get_provider(config)

        from policyforge.llm import ledger

        if batch:
            from policyforge.llm.batch import BatchError
            from policyforge.ssp.narrative import draft_narratives

            click.echo(
                "A batch is queued rather than answered, so this waits for the queue "
                "rather than for the model. Nothing is written until it ends."
            )
            # The scope names the run; each narrative inside it is recorded
            # against its own control id by the ledger's batch path, so the
            # per-control attribution survives the submission.
            with (
                ledger.run(report=click.echo, planned=len(scoped)),  # #379
                ledger.about(
                    f"ssp/{system.name or 'system'}", site="ssp", content_class=content_class
                ),
            ):
                try:
                    drafted = draft_narratives(scoped, org, system, provider, batch=True)
                except BatchError as exc:
                    raise click.ClickException(str(exc)) from exc
            missing = [c.control_id for c in scoped if c.control_id not in drafted]
            if missing:
                click.echo(
                    f"WARNING: {len(missing)} control(s) came back with no narrative: "
                    + ", ".join(missing[:5])
                    + ("..." if len(missing) > 5 else "")
                )
        else:
            # One run block around the loop: a spend ceiling is a total over
            # the run, and this is where several hundred requests are made (#125).
            with (
                ledger.run(report=click.echo, planned=len(scoped)),
                click.progressbar(
                    scoped,
                    label="Drafting narratives",
                    item_show_func=lambda c: c.control_id if c else "",
                ) as bar,
            ):
                for control in bar:
                    # One scope per control, not one for the run: this is the
                    # highest-volume path in the project, and "which controls
                    # did that model write narratives for" is the question
                    # somebody asks about a baseline of several hundred.
                    with ledger.about(control.control_id, site="ssp", content_class=content_class):
                        drafted[control.control_id] = draft_implementation_narrative(
                            control, org, system, provider
                        )

    slug = re.sub(r"[^a-z0-9]+", "-", (system.name or "system").lower()).strip("-")
    out_path = out or Path("output/ssp") / f"{slug}-ssp.xlsx"
    catalog_version = nist_controls[0].framework_version or "Rev 5"

    result = build_ssp_workbook(
        scoped,
        system=system,
        org=org,
        out_path=out_path,
        crosswalk=crosswalk,
        narratives=drafted,
        catalog_version=catalog_version,
        generated=datetime.date.today().isoformat(),
    )

    click.echo(
        f"Wrote SSP workbook -> {result.path}\n"
        f"  {result.control_count} controls, {result.enhancement_count} enhancements"
        + (f", {result.narrative_count} drafted narratives" if narratives else "")
    )
    if result.narrative_count:
        click.echo(
            "  Narratives are machine-drafted scaffolds marked "
            "'[DRAFT — REVIEW REQUIRED]' — review them before relying on this plan."
        )


def _existing_document(out_path: Path) -> str | None:
    """The text of the file `generate` would write over, or None if there is
    none. A file that can't be read as UTF-8 text can't be shown to be
    unedited and can't be recorded in history, so it stops the run."""
    try:
        if not out_path.is_file():
            return None
        return out_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise click.UsageError(
            f"{out_path} exists and can't be read as UTF-8 text ({exc}), so `generate` "
            "won't replace it. Name another file with --out."
        ) from exc


def _not_overwritten(out_path: Path, state: str) -> str:
    """The one line `generate` refuses with, naming the file and why."""
    from policyforge.content.provenance import EDITED, NO_HASH

    if state == EDITED:
        why = "was edited since it was generated"
    elif state == NO_HASH:
        why = (
            "was generated before `generate` recorded a hash of what it wrote, so an edit "
            "can't be ruled out"
        )
    else:
        why = "was not written by `generate` (it has no `generated_by` stamp)"
    return (
        f"{out_path} {why}, so it was not overwritten. Re-run with --overwrite-edits to "
        "replace it (the old file is recorded in history first), or name another file "
        "with --out."
    )


def _out_problem(out_path: Path) -> str:
    """Why `generate` couldn't write its draft to `out_path`, or "" if it can.

    Asked before any provider is built (#226), because the draft is written
    only after the model has been paid for: a path that can't be written used
    to lose that draft and end in a traceback.

    **The question is put to the filesystem for this exact target, the way
    `write_markdown` will write it** (1d on PR 241): missing parent
    directories are created one by one, then the target itself is opened,
    exclusively if it's new (and removed again) or for append if it exists
    (which leaves its contents as they are). Whatever this function created
    is removed in a `finally`, so a refused or successful check leaves
    nothing behind. A cheaper question (does the nearest directory take *a*
    file?) passed names the write then refused on Windows: `x?y.md`,
    `x<y.md`, `x|y.md`, a directory ending in a space, a 300-character name.
    Asked with the write's own operations, it agrees with the write as
    MEASURED (`test_the_check_agrees_with_the_real_write`), not by
    construction: the write opens `w`, the probe `x` or `a` (the same
    create and permission checks, without truncating). (1d on PR 241.)

    What the write already does is not a problem: it creates missing
    directories. Whether an existing file may be REPLACED is not this
    function's question: it is asked by `edit_state` (#385), because the file
    may hold a person's edits. Two answers are named before the probe, because the
    filesystem's own message for them is unclear: the target is an existing
    directory, or a FILE sits where a directory is needed on the way to it
    (the case measured on PR 225).
    """
    import contextlib

    # Even asking can fail: on Python 3.10-3.13, pathlib's `is_dir` and
    # `exists` raise for a name the OS refuses outright (ENAMETOOLONG on
    # Linux), where 3.14 returns False (9b on PR 241, CI run 551). Any
    # OSError here is the answer, not a traceback.
    try:
        if out_path.is_dir():
            return f"--out {out_path} is a directory; name the file to write."
        missing: list[Path] = []
        nearest = out_path.parent
        while not nearest.exists() and nearest != nearest.parent:
            missing.append(nearest)
            nearest = nearest.parent
        if not nearest.is_dir():
            return f"--out {out_path} can't be written: {nearest} is a file, not a directory."
    except OSError as exc:
        return f"--out {out_path} can't be written: {exc.strerror or exc}."
    created: list[Path] = []
    try:
        for directory in reversed(missing):
            directory.mkdir()
            created.append(directory)
        existed = out_path.exists()
        with out_path.open("a" if existed else "x", encoding="utf-8"):
            pass
        if not existed:
            out_path.unlink()
    except OSError as exc:
        return f"--out {out_path} can't be written: {exc.strerror or exc}."
    finally:
        for directory in reversed(created):
            with contextlib.suppress(OSError):
                directory.rmdir()
    return ""


def _run_scope(derived_from, config: dict):
    """(the catalog set a generated document's brackets are read against, the
    line saying which it is).

    **The run's set where the run has one** (80's ruling on #215): the
    catalogs `synthesize` drew the topic from, which its frontmatter records
    as `derived_from` (each a discovered framework's id). Loaded as
    `satisfies --controls` loads them (`load_catalogs`), so a bracket is a
    citation here exactly when `satisfies` given those catalogs resolves it.

    **Every catalog on disk otherwise**: a synthesis that records no set
    (written before `derived_from` travelled), or one naming a catalog that
    isn't on disk now (a file passed by path, or one since removed). A
    partial set would read a citation of the missing catalog as a role, so
    the whole set falls back, and the line names why.
    """
    from policyforge.content.tags import disk_scope
    from policyforge.frameworks.registry import catalogs_by_id
    from policyforge.topics.satisfies import catalog_scope

    recorded = [str(s) for s in derived_from or []]
    if not recorded:
        return disk_scope(), (
            "Citations read against every catalog on disk: the synthesis records no "
            "catalog set (derived_from)."
        )
    found = catalogs_by_id(config)
    missing = [s for s in recorded if s not in found or not found[s].has_controls]
    if missing:
        return disk_scope(), (
            "Citations read against every catalog on disk: the synthesis's catalog set "
            f"names {', '.join(missing)}, which is not a catalog on disk."
        )
    controls = load_catalogs([found[s].controls_path for s in recorded])
    named = ", ".join(recorded)
    return catalog_scope(controls, f"the synthesis's catalogs ({named})"), (
        f"Citations read against the synthesis's catalogs: {named}."
    )


def _run_sources(derived_from, config: dict):
    """The catalog text a Standard's source blocks quote (#17): the same set
    `_run_scope` reads its citations against, with each catalog's licence.

    The synthesis's own catalogs where it records them all on disk, every
    catalog on disk otherwise (`catalogs_by_id`, the walk `controls_on_disk`
    takes). A catalog that doesn't declare itself public is licensed, as
    `frameworks.registry` reads a manifest (fail closed)."""
    from policyforge.content.sources import source_index_of
    from policyforge.frameworks.registry import catalogs_by_id

    found = catalogs_by_id(config)
    recorded = [str(s) for s in derived_from or []]
    if recorded and all(s in found and found[s].has_controls for s in recorded):
        return source_index_of(found[s] for s in recorded)
    # Overlays change crosswalk mappings, never a control's text, so the
    # quotes need no `load_catalogs`: `check` reads the same index (#17 2b).
    return source_index_of(f for f in found.values() if f.has_controls)


def _echo_bracketed_prose(prose: list[str], *, prefix: str = "") -> None:
    """The bracketed spans the placeholder reader left as written because they
    read as sentences (267). Their own heading, and no advice to fill them:
    they are not roles or parameter values."""
    if prose:
        click.echo(f"{prefix}Bracketed prose, left as written ({len(prose)}): " + ", ".join(prose))


def _catalog_keys(sources) -> dict:
    """`generated_by`'s catalog keys (443 part 2, 80's ruling R2).

    `sources` is each input the draft was made from, as (its name, the
    `catalogs` it recorded): a synthesis's own key, or a Standard's
    `generated_by.catalogs` for a Policy drafted from its Standards. The
    catalogs are listed once each. **An input that recorded none is named
    under `catalogs_not_recorded`**, so a document drawn from a synthesis
    written before catalogs were traced says so, rather than reading as drawn
    from no catalog."""
    catalogs: list = []
    unrecorded: list[str] = []
    for name, recorded in sources:
        if not isinstance(recorded, list) or not recorded:
            unrecorded.append(name)
            continue
        catalogs.extend(entry for entry in recorded if entry not in catalogs)
    keys: dict = {"catalogs": catalogs}
    if unrecorded:
        keys["catalogs_not_recorded"] = unrecorded
    return keys


def _policy_group(name: str, topics_path: Path | None):
    """The Policy `--policy` names and the Standards under it (22, part 2b).

    Found before any model is built, and refused whole: a Policy drafted over
    a subset of its Standards would read as complete, so a topic that names the
    Policy but has no Standard in the content tree yet (or two) stops the run
    and says which.
    """
    from policyforge.generate.policy_group import find_standards
    from policyforge.topics.registry import DEFAULT_TOPICS_PATH, TopicRegistryError, load_registry

    path = topics_path or DEFAULT_TOPICS_PATH
    if not path.exists():
        raise click.UsageError(
            f"--policy reads the Policy and its topics from the registry, and {path} does "
            "not exist. Pass --topics."
        )
    try:
        registry = load_registry(path)
    except TopicRegistryError as exc:
        raise click.UsageError(f"{path} could not be read: {exc}") from exc
    policy = registry.policy(name)
    if policy is None:
        declared = ", ".join(repr(p.name) for p in registry.policies) or "none"
        raise click.UsageError(
            f"{path} declares no Policy named {name!r} (it declares: {declared})."
        )
    group = find_standards(registry, policy, _content_dir(None))
    problems: list[str] = []
    if not registry.standards_of(policy.name):
        problems.append(f"No topic in {path} names Policy {policy.name!r}, so it has no Standards.")
    if group.missing:
        problems.append(
            f"Policy {policy.name!r} would be drafted over a subset: these topics name it but "
            "have no Standard in the content tree yet. Generate them first: "
            + ", ".join(group.missing)
        )
    for topic, files in group.ambiguous.items():
        problems.append(
            f"Topic {topic!r} has more than one Standard ({', '.join(files)}), so which one "
            f"Policy {policy.name!r} governs is not decided. Remove or retire all but one."
        )
    if problems:
        raise click.UsageError(chr(10).join(problems))
    return group


def _registry_policy(topic_name: str, from_synthesis: str) -> str:
    """The Policy a Standard names as its parent (22, part 2; 80's 5919).

    **The registry's, when it can be read and lists the topic**: it is the
    truth the synthesis only snapshotted, and a synthesis written before the
    key existed (or by `synthesize --topic` with no registry) would
    otherwise make `generate` write "no parent Policy" over a Policy the
    registry names (ba on PR 439). A registry that can't be read, or doesn't
    list the topic, leaves the synthesis's value. A difference is said.
    """
    from policyforge.topics.registry import DEFAULT_TOPICS_PATH, TopicRegistryError, load_topics

    if not DEFAULT_TOPICS_PATH.exists():
        return from_synthesis
    try:
        registered = load_topics(DEFAULT_TOPICS_PATH)
    except TopicRegistryError:
        return from_synthesis
    match = next((t for t in registered if t.name.lower() == topic_name.lower()), None)
    if match is None:
        return from_synthesis
    current = match.policy.strip()
    if current != from_synthesis.strip():
        was = (
            f"the synthesis names Policy {from_synthesis!r}"
            if from_synthesis
            else ("the synthesis names none")
        )
        now = f"Policy {current!r}" if current else "no Policy"
        click.echo(f"The parent Policy is {DEFAULT_TOPICS_PATH}'s: {was}; it names {now}.")
    return current


#: The catalog the acceptable-use kind takes its controls from, and the
#: crosswalk its appendix reads (418).
ACCEPTABLE_USE_CATALOG = Path("data/frameworks/nist-800-53-r5/controls.json")
ACCEPTABLE_USE_CROSSWALK = Path("data/frameworks/crosswalk.json")
ACCEPTABLE_USE_PARAMETERS = Path("config/parameters.yaml")


def _acceptable_use_owner(topics_path: Path | None) -> str:
    """The owner of the topic that anchors PL-4, the rules of behaviour (5926),
    from the organisation's own registry. With none, the draft says
    [Responsible Team]: an example registry's owner is nobody's."""
    from policyforge.topics.anchoring import canonical_anchor
    from policyforge.topics.registry import DEFAULT_TOPICS_PATH, TopicRegistryError, load_topics

    path = topics_path or DEFAULT_TOPICS_PATH
    if not path.exists():
        click.echo(f"No topic registry at {path}, so the owner is [Responsible Team].")
        return ""
    try:
        topics = load_topics(path)
    except TopicRegistryError as exc:
        raise click.UsageError(f"{path} could not be read: {exc}") from exc
    for topic in topics:
        if "PL-4" in {canonical_anchor(a) for a in topic.nist_controls}:
            click.echo(f"Owner from topic {topic.name!r}, which anchors PL-4: {topic.owner}")
            return topic.owner
    click.echo(f"No topic in {path} anchors PL-4, so the owner is [Responsible Team].")
    return ""


def _generate_acceptable_use_cmd(
    *,
    topics_path: Path | None,
    out: Path | None,
    history_dir: Path,
    overwrite_edits: bool,
    max_cost: float | None,
):
    """`generate --document acceptable-use`: the Acceptable Use Policy and its
    Privileged User Rules addendum (418; 80's 5926 and 6334).

    Everything that can refuse does so before a provider is built: the
    destinations, an edited file, the catalog, the crosswalk, the boundary.
    """
    import frontmatter

    from policyforge.content.provenance import (
        existing_metadata,
        merge_frontmatter,
        stamp_with_body_hash,
    )
    from policyforge.content.tree import TIER_DIRECTORY
    from policyforge.export.markdown_exporter import check_markdown_quality, write_markdown
    from policyforge.generate.acceptable_use import (
        ADDENDUM_SECTIONS,
        ADDENDUM_TITLE,
        AUP_TITLE,
        assemble,
        control_texts,
        draft,
        fixed_controls,
    )
    from policyforge.generate.policy_writer import OrgContext
    from policyforge.history.version_store import record_version
    from policyforge.ingest.schema import load_controls
    from policyforge.llm import boundary, ledger
    from policyforge.org.context import apply_substitutions
    from policyforge.textfile import loads_frontmatter

    directory = _content_dir(None) / TIER_DIRECTORY["policy"]
    if out is not None and out.suffix == ".md":
        main_out, addendum_out = out, out.parent / "privileged-user-rules.md"
    else:
        base = out if out is not None else directory
        main_out, addendum_out = base / "acceptable-use.md", base / "privileged-user-rules.md"
    for path in (main_out, addendum_out):
        problem = _out_problem(path)
        if problem:
            raise click.UsageError(problem)
        existing_text = _existing_document(path)
        if existing_text is not None and not overwrite_edits:
            state = edit_state(existing_text)
            if state != UNEDITED:
                raise click.UsageError(_not_overwritten(path, state))

    if not ACCEPTABLE_USE_CATALOG.exists():
        raise click.UsageError(
            f"The acceptable-use kind reads its controls from {ACCEPTABLE_USE_CATALOG}, "
            "which does not exist. Run from the project root."
        )
    from policyforge.generate.acceptable_use import aup_sections
    from policyforge.topics.scope_facts import ScopeFactError, load_scope_facts

    try:
        ai_fact = load_scope_facts(load_config()).ai
    except ScopeFactError as exc:
        raise click.UsageError(str(exc)) from exc
    aup = aup_sections(ai_fact)
    wanted = fixed_controls(aup + ADDENDUM_SECTIONS)
    controls = load_controls(ACCEPTABLE_USE_CATALOG)
    # Decided parameter values go in before drafting, as `synthesize` puts
    # them in: a control that already says "annually" is one the draft
    # restates, not one it leaves as [Assignment: ...] or decides itself.
    from policyforge.parameters.ledger import apply_to_controls, load_ledger

    decisions = load_ledger(ACCEPTABLE_USE_PARAMETERS)
    if decisions:
        controls, filled = apply_to_controls(controls, decisions)
        click.echo(f"Applied {len(decisions)} recorded parameter decision(s): {filled} filled.")
    texts = control_texts(controls, wanted)
    absent = [c for c in wanted if c not in texts]
    if absent:
        raise click.ClickException(
            f"{ACCEPTABLE_USE_CATALOG} holds no {', '.join(absent)}, which the "
            "acceptable-use kind draws on."
        )
    if ACCEPTABLE_USE_CROSSWALK.exists():
        crosswalk = _current_crosswalk(ACCEPTABLE_USE_CROSSWALK)
    else:
        crosswalk = None
        click.echo(
            f"No crosswalk at {ACCEPTABLE_USE_CROSSWALK}: the traceability appendix lists the "
            "800-53 controls only. Run `policyforge map` to add the other catalogs'."
        )

    config = budgeted(load_config(), max_cost)
    org_cfg = config.get("org", {})
    profile = load_org_profile(config)
    org = OrgContext(
        name=org_cfg.get("name", ""),
        industry=org_cfg.get("industry", ""),
        vendors=profile.unkeyed_vendors,
        profile=profile,
    )
    owner = _acceptable_use_owner(topics_path)
    if ai_fact is None:
        click.echo("Included the AI Use section: no 'ai' scope fact exists yet.")
    elif ai_fact == "none":
        click.echo("Left out the AI Use section: `programme.ai` is none.")
    else:
        click.echo(f"Included the AI Use section: `programme.ai` is {ai_fact}.")

    # The model is shown 800-53 text only, so that catalog's class is what the
    # call is checked against. The appendix's crosswalked ids never reach it.
    content = boundary.classify_path(ACCEPTABLE_USE_CATALOG, config)
    from policyforge.library import catalog_trace

    # The one catalog both documents are drafted from (443 part 2).
    trace = _catalog_keys(
        [
            (
                ACCEPTABLE_USE_CATALOG.name,
                [
                    catalog_trace(
                        ACCEPTABLE_USE_CATALOG,
                        content.framework_id or ACCEPTABLE_USE_CATALOG.name,
                    )
                ],
            )
        ]
    )
    decision = boundary.check(content, boundary.classify_provider(config.get("llm") or {}), config)
    if not decision.allowed:
        raise click.ClickException(
            "Refusing to draft the Acceptable Use Policy with the configured provider.\n"
            f"{decision.explain()}\n" + boundary.refusal_advice(decision, config)
        )
    provider = get_provider(config)

    documents = [
        (main_out, "policy/acceptable-use", AUP_TITLE, aup, True),
        (addendum_out, "policy/privileged-user-rules", ADDENDUM_TITLE, ADDENDUM_SECTIONS, False),
    ]
    drafted = []
    with ledger.run(report=click.echo):
        for out_path, slug, title, sections, acknowledgment in documents:
            with ledger.about(slug, site="generate", content_class=content.klass) as scope:
                text = draft(
                    org, provider, title=title, sections=sections, texts=texts, owner=owner
                )
            assembled = assemble(
                text,
                title=title,
                sections=sections,
                crosswalk=crosswalk,
                acknowledgment=acknowledgment,
            )
            if len(assembled.missing) == len(sections):
                # Nothing the draft wrote is one of the sections: a document of
                # placeholders would read as drafted. Nothing is written (1d on
                # PR 453).
                raise click.ClickException(
                    f"The draft of the {title} named none of its sections, so nothing "
                    "was written. Its headings: "
                    + (", ".join(repr(h) for h in assembled.dropped) or "none")
                )
            if assembled.tags:
                click.echo(
                    f"{title}: removed {len(assembled.tags)} citation tag(s) from the body; "
                    "the controls are in the appendix."
                )
            if assembled.by_position:
                click.echo(
                    f"{title}: read by position, the headings naming no section: "
                    + ", ".join(f"{h!r} as {s}" for h, s in assembled.by_position)
                )
            if assembled.dropped:
                click.echo(
                    f"{title}: dropped what the draft wrote outside its sections: "
                    + ", ".join(repr(h) for h in assembled.dropped)
                )
            if assembled.missing:
                click.echo(
                    f"{title}: the draft gave no rules for {', '.join(assembled.missing)}, "
                    "so each has a placeholder to fill."
                )
            drafted.append((out_path, slug, assembled.text, scope.provenance()))

    for out_path, slug, document, provenance in drafted:
        substituted = apply_substitutions(document, profile, scope=None, owner=owner)
        document = substituted.text
        if substituted.unfilled:
            # A document staff sign, with values nobody has decided (b5 on PR 453).
            click.echo(
                f"{out_path.name}: {len(substituted.unfilled)} placeholder(s) left "
                "unfilled: " + ", ".join(substituted.unfilled)
            )
            click.echo(
                "  Fill a role under `org.vendors` or `org.teams`, or record a value in "
                "the parameters ledger (`config/parameters.yaml`), then regenerate."
            )
        _echo_bracketed_prose(substituted.prose, prefix=f"{out_path.name}: ")
        existing_text = _existing_document(out_path)
        parsed = loads_frontmatter(document)
        # No `topic:`: the document is drawn from several topics, and one
        # naming a topic would be read as that topic's Policy by edit-topic.
        parsed.metadata, owner_note = merge_frontmatter(
            parsed.metadata,
            existing_metadata(existing_text),
            tier="policy",
            topic="",
            owner=owner,
            binding=None,
            policy=None,
        )
        if owner_note:
            click.echo(owner_note)
        stamped = stamp_with_body_hash(
            frontmatter.dumps(parsed) + "\n",
            {**(provenance if provenance.get("models") else {}), **trace},
        )
        if existing_text is not None and edit_state(existing_text) != UNEDITED:
            kept = record_version(
                history_dir,
                slug,
                existing_text,
                source="replaced-by-generate",
                metadata={"path": str(out_path), "replaced_by": "generate --overwrite-edits"},
            )
            if kept is not None:
                click.echo(
                    f"Recorded the replaced {out_path} as {slug!r} v{kept.version} in "
                    f"{history_dir} (+{kept.lines_added}/-{kept.lines_removed} lines)."
                )
        written = write_markdown(stamped, output_dir=out_path.parent, filename=out_path.name)
        if not check_markdown_quality(written):
            click.echo(
                f"WARNING: {written} did not pass the mdformat quality check "
                "— review before shipping."
            )
        click.echo(f"Drafted policy -> {written}")
        record = record_version(
            history_dir,
            slug,
            document,
            source="generate",
            metadata={
                "org": org.name,
                "document_kind": "acceptable-use",
                "owner": owner,
                **provenance,
            },
        )
        if record is not None:
            click.echo(
                f"Recorded {slug!r} v{record.version} in {history_dir} "
                f"(+{record.lines_added}/-{record.lines_removed} lines)."
            )


@cli.command("generate")
@click.option(
    "--document",
    "document_kind",
    type=click.Choice(["acceptable-use"]),
    default=None,
    help="Draft a document of its own kind rather than a topic's: 'acceptable-use' writes "
    "the Acceptable Use Policy and its Privileged User Rules addendum from a fixed set of "
    "800-53 controls. Takes no --synthesis, --standard or --policy.",
)
@click.option(
    "--tier",
    type=click.Choice(["standard", "policy", "procedure"]),
    default="standard",
    help="Document tier to draft. 'policy' and 'procedure' require --standard, so they "
    "can reference the Standard document by name; 'policy' with --policy drafts one "
    "Policy over every Standard that names it instead.",
)
@click.option(
    "--synthesis",
    "synthesis_path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a synthesized topic markdown file produced by `policyforge synthesize`. "
    "Required unless --policy is given.",
)
@click.option(
    "--standard",
    "standard_path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the already-generated Standard document this Policy or Procedure "
    "implements. Required when --tier policy or --tier procedure, except with --policy.",
)
@click.option(
    "--policy",
    "policy_name",
    default=None,
    help="Draft one Policy over every Standard whose topic names it (--tier policy). "
    "Reads the Policy and its topics from the registry (--topics), and the Standards "
    "from the content tree.",
)
@click.option(
    "--topics",
    "topics_path",
    default=None,
    type=click.Path(path_type=Path),
    help="The topic registry --policy reads (default: config/topics.yaml).",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to write the drafted document (default: docs/<tier>s/<synthesis-filename>).",
)
@click.option(
    "--history-dir",
    default=_DEFAULT_HISTORY_DIR,
    type=click.Path(path_type=Path),
    help="Where local version history is recorded (default: output/.history). "
    "See `policyforge history`.",
)
@click.option(
    "--overwrite-edits",
    is_flag=True,
    default=False,
    help="Replace the target even though it was edited since `generate` wrote it, or was "
    "never written by `generate`. The replaced file is recorded in history first.",
)
@max_cost_option
def generate_cmd(
    document_kind: str | None,
    tier: str,
    synthesis_path: Path | None,
    standard_path: Path | None,
    policy_name: str | None,
    topics_path: Path | None,
    out: Path | None,
    history_dir: Path,
    overwrite_edits: bool,
    max_cost: float | None,
):
    """Draft a Standard, Policy, or Procedure document from a synthesized topic."""
    if document_kind is not None:
        if synthesis_path is not None or standard_path is not None or policy_name is not None:
            raise click.UsageError(
                "--document drafts a standalone document: it takes neither "
                "--synthesis, --standard, nor --policy."
            )
        return _generate_acceptable_use_cmd(
            topics_path=topics_path,
            out=out,
            history_dir=history_dir,
            overwrite_edits=overwrite_edits,
            max_cost=max_cost,
        )

    from policyforge.export.markdown_exporter import check_markdown_quality, write_markdown
    from policyforge.generate.policy_group import (
        assemble_policy,
        generate_policy_group,
        group_content_class,
        policy_slug,
        render_standards,
    )
    from policyforge.generate.policy_writer import (
        OrgContext,
        TopicContext,
        extract_title,
        generate_policy,
        generate_procedure,
        generate_standard,
    )
    from policyforge.synthesis.merge import read_synthesis

    # Every check that needs no model, first (#220): before config, catalogs
    # or a provider, so a wrong argument is named as one rather than failing
    # on a provider that can't be built, or after building one it never uses.
    standard_title = ""
    group = None
    if policy_name is not None:
        if tier != "policy":
            raise click.UsageError(
                f"--policy drafts a Policy, so it needs --tier policy (this is --tier {tier})."
            )
        if synthesis_path is not None or standard_path is not None:
            raise click.UsageError(
                "--policy drafts from the Standards in the content tree: it takes neither "
                "--synthesis nor --standard."
            )
        group = _policy_group(policy_name, topics_path)
    elif synthesis_path is None:
        raise click.UsageError("--synthesis is required (or --policy NAME with --tier policy).")
    if group is None and tier in ("policy", "procedure"):
        if standard_path is None:
            raise click.UsageError(
                f"--standard is required when --tier {tier}, so the {tier.capitalize()} "
                "can reference its Standard document by name."
            )
        try:
            standard_text = standard_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise click.UsageError(
                f"--standard {standard_path} can't be read as a UTF-8 text file: {exc}"
            ) from exc
        try:
            standard_title = extract_title(standard_text)
        except ValueError as exc:
            raise click.UsageError(
                f"--standard {standard_path} has no '# ' title, which the {tier.capitalize()} "
                "cites it by. Point --standard at a Standard `generate` drafted."
            ) from exc

    from policyforge.content.tree import TIER_DIRECTORY

    # Where the draft goes, checked now (#226): it's written only after the
    # model call, so an unwritable path would lose a paid draft.
    # Writes into the shared content tree (_content_dir(), #385), under
    # the directory `check` reads this tier from (#411).
    default_name = policy_slug(group.policy.name) + ".md" if group else synthesis_path.name
    out_path = out or _content_dir(None) / TIER_DIRECTORY[tier] / default_name
    problem = _out_problem(out_path)
    if problem:
        raise click.UsageError(problem)

    # A document already there may hold a person's edits, and the content tree
    # is where those live (#385, 80's ruling): it is replaced only if it is
    # exactly what `generate` last wrote there, or `--overwrite-edits` says so.
    # Asked now, before the model call, so a refusal never costs a draft.
    existing_text = _existing_document(out_path)
    if existing_text is not None and not overwrite_edits:
        state = edit_state(existing_text)
        if state != UNEDITED:
            raise click.UsageError(_not_overwritten(out_path, state))

    config = budgeted(load_config(), max_cost)
    org_cfg = config.get("org", {})
    profile = load_org_profile(config)
    org = OrgContext(
        name=org_cfg.get("name", ""),
        industry=org_cfg.get("industry", ""),
        vendors=profile.unkeyed_vendors,
        profile=profile,
    )
    if profile.unknown:
        click.echo(
            "WARNING: config names roles this version does not know: "
            + ", ".join(profile.unknown)
            + " — run `policyforge roles` for the list. They were ignored."
        )

    # Topic ownership travels in the synthesis file's frontmatter, written by
    # `synthesize --topic-name`. Files without it still work — they just draft
    # with [Responsible Team] placeholders, as before.
    if group is not None:
        # No synthesis: the Policy is drafted from its Standards (22, part 2b).
        metadata, topic_synthesis = {}, ""
        topic_context = TopicContext(
            owner=group.policy.owner,
            cadence=group.policy.cadence,
            policy=group.policy.name,
        )
        drafted_from = f"the Standards under Policy {group.policy.name!r}"
    else:
        metadata, topic_synthesis = read_synthesis(synthesis_path.read_text(encoding="utf-8"))
        topic_context = TopicContext(
            name=str(metadata.get("topic") or ""),
            owner=str(metadata.get("owner") or ""),
            cadence=str(metadata.get("cadence") or ""),
            evidence=list(metadata.get("evidence") or []),
            playbook=list(metadata.get("playbook") or []),
            policy=str(metadata.get("policy") or ""),
        )
        drafted_from = str(synthesis_path)
    if tier == "standard" and topic_context.name:
        topic_context.policy = _registry_policy(topic_context.name, topic_context.policy)
    if group is not None:
        if group.policy.owner:
            click.echo(f"Policy owner from the registry: {group.policy.owner}")
        else:
            click.echo(
                f"Policy {group.policy.name!r} declares no owner in the registry, so the "
                "draft will use [Responsible Team]."
            )
    elif topic_context.owner:
        click.echo(f"Topic owner from synthesis frontmatter: {topic_context.owner}")
    else:
        click.echo(
            "No owner in the synthesis frontmatter — the draft will use "
            "[Responsible Team]. Re-run `synthesize --topic-name` to record one."
        )
    if group is None and not topic_context.name:
        click.echo("No topic in the synthesis frontmatter.")

    # The class the synthesis was drawn from, checked before any provider is
    # built. `classify_path` would call this file the organization's own —
    # it is a file under output/ — and that is exactly how a restatement of
    # HITRUST requirement text used to reach a hosted model one command after
    # `synthesize` had refused to send the HITRUST text itself.
    from policyforge.content.provenance import declared_content_class, read_generated_by
    from policyforge.llm import boundary

    # Both places a synthesis can state its class, the most guarded winning
    # (#339, b5 on PR 343): reading the top-level key alone let
    # `content_class: public-domain` lower a licensed `generated_by` stamp
    # here, the one reader that decides a model call from a synthesis.
    # WHETHER a class was stated is decided as before (either key present),
    # so a stated value that isn't a known class, an empty one included, is
    # still refused below, not guessed.
    stated = (
        metadata.get("content_class") is not None
        or read_generated_by(metadata).get("content_class") is not None
    )
    recorded = declared_content_class(metadata) if stated else None
    if recorded is not None:
        klass = recorded
        if klass not in boundary.CONTENT_CLASSES:
            raise click.ClickException(
                f"{synthesis_path} says its content_class is {recorded!r}, which is not a "
                f"content class ({', '.join(boundary.CONTENT_CLASSES)}). Refusing rather "
                "than guessing — re-run `synthesize` to write it again."
            )
        sources = ", ".join(str(s) for s in metadata.get("derived_from") or []) or "not recorded"
        content = boundary.ContentClassification(
            klass, f"the synthesis frontmatter says so (derived from: {sources})"
        )
    elif group is not None:
        # The Policy is drawn from its Standards: the most guarded of them is
        # what the model call is checked against.
        content = group_content_class(group, config)
    else:
        # Written before the class travelled. Classified as it always was,
        # which is the most this file can say about itself.
        content = boundary.classify_path(synthesis_path, config)
    decision = boundary.check(content, boundary.classify_provider(config.get("llm") or {}), config)
    if not decision.allowed:
        raise click.ClickException(
            f"Refusing to draft from {drafted_from} with the configured provider.\n"
            f"{decision.explain()}\n"
            + boundary.refusal_advice(
                decision,
                config,
                otherwise=(
                    "  Point `llm:` at a local model for this run, or — if this provider really "
                    "is inside your boundary — declare that with `llm.classification`."
                ),
            )
        )
    content_class = content.klass

    # Built before any provider call, so a catalog that can't be read stops
    # the run before it spends anything rather than after the draft exists.
    if group is not None:
        from policyforge.content.tags import disk_scope

        # A Policy is drafted from Standards, not from a catalog set: brackets
        # are read against every catalog on disk, and none survive into it.
        citation_scope, scope_note = disk_scope(), "Citations read against every catalog on disk."
        standards_text, cut = render_standards(group)
        if cut:
            click.echo(
                f"{len(cut)} Standard(s) were cut before the model saw them "
                "(over the length a Policy is drafted from): " + ", ".join(cut)
            )
    else:
        citation_scope, scope_note = _run_scope(metadata.get("derived_from"), config)

    provider = get_provider(config)

    from policyforge.llm import ledger

    # Every call the drafting makes is attributed to this document, so the
    # version-history stamp below names the model that actually wrote it
    # rather than the one config happened to hold. Those differ whenever a
    # cascade escalates, which is exactly when the difference matters.
    slug = f"{tier}/{out_path.stem}"

    # The class goes on the scope as well as through the check above, so each
    # call's ledger record says what it carried and the provenance stamped
    # into the version history says what the document was drawn from.
    with (
        ledger.run(report=click.echo),  # #379: what the run cost, said once
        ledger.about(slug, site="generate", content_class=content_class) as scope,
    ):
        if group is not None:
            document = generate_policy_group(
                org, provider, group=group, standards_text=standards_text
            )
        elif tier in ("policy", "procedure"):
            # `standard_title` was read and checked before the provider (#220).
            generator = generate_policy if tier == "policy" else generate_procedure
            document = generator(
                topic_synthesis,
                org,
                provider,
                standard_title=standard_title,
                topic=topic_context,
            )
        else:
            from policyforge.generate.playbook_repair import PlaybookRepairFailed
            from policyforge.org.context import org_actors

            try:
                document = generate_standard(
                    topic_synthesis,
                    org,
                    provider,
                    topic=topic_context,
                    org_actors=org_actors(config),
                )
            except PlaybookRepairFailed as exc:
                raise click.ClickException(str(exc)) from exc

    # Fill the role placeholders here rather than trusting the prompt to have
    # done it consistently. Same document plus same config gives the same
    # output every time, with no model in the loop.
    from policyforge.org.context import apply_substitutions

    substituted = apply_substitutions(
        document,
        profile,
        citation_scope,
        # The Standard a Procedure names is its reference, not a placeholder (#266).
        ignore=[standard_title] if standard_title else None,
        owner=topic_context.owner,
    )
    click.echo(scope_note)
    document = substituted.text
    if substituted.filled:
        pairs = sorted({f"{label} -> {value}" for label, value in substituted.filled})
        click.echo(f"Filled {len(substituted.filled)} role placeholder(s): " + ", ".join(pairs))
    # Three lists, each with advice that fits (#222, #228): a declared role is
    # filled in config; anything else unfilled may be a role the vocabulary
    # doesn't declare or a parameter value, and nothing here can tell which,
    # so its advice names both places; a citation that doesn't resolve is
    # fixed in the tag or the catalog set. Together they are every bracket
    # left, as one list was before.
    roles_left = substituted.roles_left
    if roles_left:
        click.echo(f"{len(roles_left)} role(s) left as placeholders: " + ", ".join(roles_left))
        click.echo(
            "  Add them under `org.vendors` or `org.teams` (`policyforge roles` lists "
            "the keys), then regenerate."
        )
    if substituted.unfilled:
        click.echo(
            f"{len(substituted.unfilled)} placeholder(s) left unfilled: "
            + ", ".join(substituted.unfilled)
        )
        click.echo(
            "  Fill a role under `org.vendors` or `org.teams`, or record a value in the "
            "parameters ledger (`config/parameters.yaml`), then regenerate."
        )
    _echo_bracketed_prose(substituted.prose)
    if substituted.unresolved_citations:
        click.echo(
            f"{len(substituted.unresolved_citations)} citation(s) that don't resolve against "
            "this run's catalogs: " + ", ".join(substituted.unresolved_citations)
        )
        # A role written in citation shape (`[Tier 2 Support]`) lands here too
        # (80's ruling 3707 on PR 227), so the advice covers it.
        click.echo(
            "  Check each tag's spelling, or add that framework's catalog to the run, "
            "then regenerate. If one is a role, not a citation, add it under "
            "`org.vendors` or `org.teams`."
        )

    if group is not None:
        # What a reader relies on to find the Standards is written by code, not
        # the model: each Standard has its section, and the closing list is
        # built from the files (22, part 2b).
        assembled = assemble_policy(document, group, citation_scope, link_from=out_path.parent)
        document = assembled.text
        if assembled.tags_removed:
            click.echo(
                f"Removed {len(assembled.tags_removed)} citation tag(s) from the Policy: it "
                "carries none; what it rests on is the Standards it names."
            )
        if assembled.tags_unresolved:
            click.echo(
                f"Removed {len(assembled.tags_unresolved)} tag-shaped span(s) that don't "
                "resolve in the catalogs on disk: " + ", ".join(assembled.tags_unresolved)
            )
        if assembled.lists_dropped:
            click.echo(
                f"Dropped {len(assembled.lists_dropped)} section(s) the draft wrote: a list "
                "of Standards (the closing list is written from the files), or one headed "
                "as a Standard this Policy does not govern: "
                + ", ".join(repr(h) for h in assembled.lists_dropped)
            )
        if assembled.omitted:
            click.echo(
                f"{len(assembled.omitted)} Standard(s) had no section in the draft, so each was "
                "given a plain one naming it: " + ", ".join(assembled.omitted)
            )
        if assembled.foreign:
            click.echo(
                "WARNING: the draft names Standard(s) this Policy does not govern: "
                + ", ".join(assembled.foreign)
                + ". Edit the sentence, or regenerate."
            )
        click.echo(
            f"Standards under this Policy: {len(group.standards)} "
            f"({', '.join(s.title for s in group.standards)})."
        )

    if tier == "standard":
        # The citation form and source wording the user asked for (#17, 80's
        # 6048): deterministic, from the run's catalogs, after the model call.
        # Done after the brackets above are read, so their reports are of the
        # document the model wrote; the blocks add no tags.
        from policyforge.content.sources import add_source_blocks, normalize_tags
        from policyforge.generate.policy_writer import with_parent_policy

        # The Standard names its parent Policy in one sentence (22, part 2;
        # 80's 5919), or says it has none. Written here, not asked of the
        # model, so it is always there, and is the registry's name for it.
        document, titled = with_parent_policy(document, topic_context.policy)
        if not titled:
            click.echo(
                "The draft has no '# ' title, so the parent-Policy sentence is its first line."
            )
        document = normalize_tags(document, citation_scope)
        # Only YAML `true` quotes licensed text (b5 on PR 430): `bool()` read
        # the string "false" as on, and put a licensed catalog's text in the
        # Standard. Anything that isn't a boolean is off, and said.
        setting = (config.get("standards") or {}).get("quote_licensed", False)
        quote_licensed = setting is True
        if not isinstance(setting, bool) and setting is not None:
            click.echo(
                f"standards.quote_licensed is {setting!r}, not true or false, so licensed "
                "text is not quoted. Write `true` (no quotes) to quote it."
            )
        sources = _run_sources(metadata.get("derived_from"), config)
        document, blocks = add_source_blocks(
            document, sources, citation_scope, quote_licensed=quote_licensed
        )
        if blocks:
            withheld = sources.licensed and not quote_licensed
            click.echo(
                f"Added {blocks} source block(s): each section's cited requirements, "
                "in the catalogs' own words"
                + (
                    "; licensed catalogs by id and title only (standards.quote_licensed is off)."
                    if withheld
                    else "."
                )
            )

    if tier == "procedure":
        # A Procedure's body carries no inline tags (the user's review of a
        # 20-topic run, #22): one section maps each step to the Standard
        # section it implements. Done after the brackets above are read, so
        # their reports are of the document the model wrote.
        from policyforge.content.traceability import trace_procedure
        from policyforge.textfile import loads_frontmatter

        traced = trace_procedure(
            document, loads_frontmatter(standard_text).content, standard_title, citation_scope
        )
        document = traced.text
        if traced.written:
            click.echo(
                f"Traced {traced.sections - len(traced.unmatched)} of {traced.sections} "
                f"Procedure section(s) to {standard_title!r}, and moved "
                f"{traced.moved} citation tag(s) into its Traceability section."
            )
        if traced.unmatched:
            shown = ", ".join(traced.unmatched[:5]) + (" ..." if len(traced.unmatched) > 5 else "")
            click.echo(
                f"{len(traced.unmatched)} Procedure section(s) reach no section of the "
                f"Standard: {shown}"
            )
            if not traced.written:
                click.echo(
                    "  No section reaches the Standard, so this Procedure has no traceability "
                    "section and keeps any citation it carries. Check that the Standard and "
                    "the Procedure were drafted from the same synthesis."
                )
        if traced.left:
            # Citations the pass did not reach, said where the user will see
            # them rather than left for `check` (1d's review of #22): the model
            # wrote its steps as a numbered list or under `####`, not as `###`
            # sections, or put a citation outside any step.
            why = (
                "no `###` step sections were found under 'Procedure Steps', so no "
                "Traceability section was written; the steps may be a list or under `####`"
                if traced.sections == 0
                else "they sit outside the step sections"
            )
            click.echo(f"{traced.left} citation tag(s) are still inline in this Procedure: {why}.")

    # Stamped into the document, not just into the gitignored local history.
    # The question "which documents did that model touch" has to be
    # answerable from a checkout by someone who was not here — see
    # content/provenance.py. Written before the quality check so that what
    # mdformat judges is the file that ships.
    # Topic, owner, tier and any Confluence binding travel in the document's
    # frontmatter (#384), merged with the provenance stamp so publish, check
    # and Zardoz can tell what each document is and who owns it.
    import frontmatter

    from policyforge.content.provenance import (
        existing_metadata,
        merge_frontmatter,
        stamp_with_body_hash,
    )
    from policyforge.textfile import loads_frontmatter
    from policyforge.topics.registry import DEFAULT_TOPICS_PATH, TopicRegistryError, load_topics

    confluence_binding: dict | None = None
    if group is not None:
        # The page a Policy is published to is declared on the Policy itself.
        page = group.policy.page("confluence")
        if page:
            container, page_title = page
            confluence_binding = (
                {"space": container, "title": page_title} if container else {"title": page_title}
            )
    elif DEFAULT_TOPICS_PATH.exists() and topic_context.name:
        try:
            registry = load_topics(DEFAULT_TOPICS_PATH)
        except TopicRegistryError as exc:
            # Not fatal here: the draft is already paid for, and losing it
            # over a binding would be worse. But never silent: the document
            # goes out without its `confluence:` block and says why.
            click.echo(
                f"{DEFAULT_TOPICS_PATH} could not be read ({exc}), so no confluence: "
                "binding was written for this document."
            )
            registry = []
        match = next((t for t in registry if t.name.lower() == topic_context.name.lower()), None)
        if match:
            target = match.target("confluence")
            page_title = (target.get("pages") or {}).get(tier)
            if page_title:
                space = target.get("space")
                confluence_binding = (
                    {"space": space, "title": page_title} if space else {"title": page_title}
                )

    # Merged with the frontmatter already at the target, as `pull` merges
    # (#404): `generate` updates the keys it owns (the stamp, topic, tier) and
    # keeps every other, a hand-set owner and the binding's pulled version
    # included. The body hash covers the body only, so this is what keeps a
    # frontmatter-only edit from being replaced without a word.
    parsed = loads_frontmatter(document)
    earlier_meta = existing_metadata(existing_text)
    if group is not None and earlier_meta.get("topic"):
        # The file was a single topic's Policy: this one is the group's, so
        # what belonged to that topic's page is not carried onto it.
        click.echo(
            f"{out_path} was the Policy drafted for topic {str(earlier_meta['topic'])!r}; "
            "its topic, owner and page binding are not carried onto the group's Policy."
        )
        earlier_meta = {
            k: v
            for k, v in earlier_meta.items()
            if k not in ("topic", "confluence", "targets", "owner")
        }
    if group is not None:
        policy_key = group.policy.name
    else:
        policy_key = topic_context.policy if tier == "standard" else None
    parsed.metadata, owner_note = merge_frontmatter(
        parsed.metadata,
        earlier_meta,
        tier=tier,
        topic=topic_context.name,
        owner=topic_context.owner,
        binding=confluence_binding,
        policy=policy_key,
    )
    if owner_note:
        click.echo(owner_note)

    with_meta = frontmatter.dumps(parsed) + "\n"
    provenance = scope.provenance()
    # Every document carries the hash of its body (#385), so the next run can
    # tell an edit from what it wrote. With no model recorded there is no
    # attribution to make, and the block holds the hash and the catalogs.
    if group is not None:
        drawn_from = [
            (s.relative, read_generated_by(s.metadata).get("catalogs")) for s in group.standards
        ]
    else:
        drawn_from = [(synthesis_path.name, metadata.get("catalogs"))]
    stamped = stamp_with_body_hash(
        with_meta,
        {**(provenance if provenance.get("models") else {}), **_catalog_keys(drawn_from)},
    )

    from policyforge.history.version_store import record_version

    # The replaced file goes into history BEFORE it is replaced, and the run
    # says so. Only `--overwrite-edits` gets here with anything to keep.
    if existing_text is not None and edit_state(existing_text) != UNEDITED:
        kept = record_version(
            history_dir,
            slug,
            existing_text,
            source="replaced-by-generate",
            metadata={"path": str(out_path), "replaced_by": "generate --overwrite-edits"},
        )
        if kept is None:
            click.echo(
                f"The replaced {out_path} is already the latest recorded version of {slug!r}."
            )
        else:
            click.echo(
                f"Recorded the replaced {out_path} as {slug!r} v{kept.version} in {history_dir} "
                f"(+{kept.lines_added}/-{kept.lines_removed} lines)."
            )
    written = write_markdown(stamped, output_dir=out_path.parent, filename=out_path.name)
    if not check_markdown_quality(written):
        click.echo(
            f"WARNING: {written} did not pass the mdformat quality check — review before shipping."
        )
    click.echo(f"Drafted {tier} -> {written}")

    # The provenance stamp replaces a bare `model` read out of config. The
    # day a model is found to systematically weaken cited requirements — the
    # failure `content/deontic.py` detects — the question is which documents
    # it touched, and config's answer is only ever what was configured most
    # recently. This one is what answered, with the prompt hashes that
    # produced it, at no cost at generation time.
    record = record_version(
        history_dir,
        slug,
        document + "\n",
        source="generate",
        metadata={
            "org": org.name,
            "synthesis_source": drafted_from,
            **(
                {
                    "policy": group.policy.name,
                    "standards": [standard.relative for standard in group.standards],
                }
                if group is not None
                else {}
            ),
            **scope.provenance(),
        },
    )
    if scope.models:
        click.echo(f"Drafted by: {', '.join(scope.models)} ({len(scope.records)} call(s))")
    if record is None:
        # `record_version` compares the generated TEXT with the last recorded
        # one. The file on disk can still have changed (its frontmatter, or an
        # edit just replaced); never say "no change" over a file that was.
        try:
            rewritten = existing_text is not None and existing_text != Path(written).read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError):
            rewritten = True
        if rewritten:
            click.echo(
                f"The drafted text is the same as the last recorded version of {slug!r}; "
                f"{written} was rewritten (its frontmatter) and history is unchanged."
            )
        else:
            click.echo(
                f"No content change since the last recorded version of {slug!r} "
                "— history unchanged."
            )
    else:
        click.echo(
            f"Recorded {slug!r} v{record.version} in {history_dir} "
            f"(+{record.lines_added}/-{record.lines_removed} lines)."
        )

"""Drafting with a model: synthesis, System Security Plans, policy documents."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import (
    _DEFAULT_HISTORY_DIR,
    get_provider,
    load_catalogs,
    load_config,
)
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
@click.option(
    "--controls",
    "controls_paths",
    required=True,
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a controls.json (one per loaded framework). Repeatable.",
)
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
def synthesize_cmd(
    topic_name: str | None,
    topics_path: Path,
    topic: str | None,
    nist_controls: str | None,
    controls_paths,
    crosswalk_path: Path,
    parameters_path: Path,
    out_dir: Path,
):
    """Merge/dedupe controls for one topic into synthesized requirement prose.

    Either `--topic-name <registry topic>`, which takes the anchor controls and
    the owning team from config/topics.yaml, or `--topic` plus
    `--nist-controls` for a one-off topic that isn't in the registry.
    """
    import json
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

    owner = cadence = ""
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
    elif topic and nist_controls:
        nist_ids = [c.strip() for c in nist_controls.split(",") if c.strip()]
    else:
        raise click.UsageError(
            "Give either --topic-name (a topic from config/topics.yaml) or both "
            "--topic and --nist-controls."
        )

    config = load_config()

    # Synthesis sends control statements to a model, so every catalog named
    # here is content leaving for the provider. A repository holding a
    # licensed HITRUST export under `local_content/` — which this project
    # tells people to do — can pass it to `--controls` as easily as it can
    # pass 800-53, and until this check the two were indistinguishable.
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

    # The catalogs above carry the organization's crosswalk overlays, but
    # crosswalk.json is whatever `map` wrote last. Built from different
    # overlays — before one was added, edited or deleted — it would pull a
    # rejected pair's requirement into the synthesis or miss a restored one,
    # so it is refused rather than used. Compared by content: a modification
    # time can tie, and a deleted overlay has none.
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
    crosswalk = json.loads(crosswalk_path.read_text(encoding="utf-8"))
    synthesis_topic = build_synthesis_topic(topic, nist_ids, controls, crosswalk)
    if not synthesis_topic.controls:
        click.echo(f"No controls found for topic {topic!r} — check its anchors and --controls.")
        raise SystemExit(1)

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
        ledger.run(report=click.echo),  # #379: what the run cost, said once
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
            # NIST's suggested actions for the AI RMF subcategories this topic
            # anchors, in the frontmatter so only the Standard reads them (#301).
            playbook=playbook,
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
@click.option(
    "--controls",
    "controls_paths",
    required=True,
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a controls.json. Repeatable - pass the NIST 800-53 file plus any "
    "other framework whose mappings you want shown as crosswalk columns.",
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
def ssp_cmd(
    controls_paths,
    baseline: str | None,
    system_name: str | None,
    narratives: bool,
    batch: bool,
    yes: bool,
    out: Path | None,
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

    config = load_config()
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
        click.echo(
            f"Drafting implementation narratives for {len(scoped)} controls — "
            f"that is {len(scoped)} requests to your configured LLM provider."
            + (" Submitted together as one batch, at half the price." if batch else "")
        )
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
                ledger.run(report=click.echo),  # #379
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
            with click.progressbar(
                scoped,
                label="Drafting narratives",
                item_show_func=lambda c: c.control_id if c else "",
            ) as bar:
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


@cli.command("generate")
@click.option(
    "--tier",
    type=click.Choice(["standard", "policy", "procedure"]),
    default="standard",
    help="Document tier to draft. 'policy' and 'procedure' require --standard, so they "
    "can reference the Standard document by name.",
)
@click.option(
    "--synthesis",
    "synthesis_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a synthesized topic markdown file produced by `policyforge synthesize`.",
)
@click.option(
    "--standard",
    "standard_path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the already-generated Standard document this Policy or Procedure "
    "implements. Required when --tier policy or --tier procedure.",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to write the drafted document (default: output/<tier>s/<synthesis-filename>).",
)
@click.option(
    "--history-dir",
    default=_DEFAULT_HISTORY_DIR,
    type=click.Path(path_type=Path),
    help="Where local version history is recorded (default: output/.history). "
    "See `policyforge history`.",
)
def generate_cmd(
    tier: str,
    synthesis_path: Path,
    standard_path: Path | None,
    out: Path | None,
    history_dir: Path,
):
    """Draft a Standard, Policy, or Procedure document from a synthesized topic."""
    from policyforge.export.markdown_exporter import check_markdown_quality, write_markdown
    from policyforge.generate.policy_writer import (
        OrgContext,
        TopicContext,
        extract_title,
        generate_policy,
        generate_procedure,
        generate_standard,
    )
    from policyforge.synthesis.merge import read_synthesis

    config = load_config()
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
    metadata, topic_synthesis = read_synthesis(synthesis_path.read_text(encoding="utf-8"))
    topic_context = TopicContext(
        name=str(metadata.get("topic") or ""),
        owner=str(metadata.get("owner") or ""),
        cadence=str(metadata.get("cadence") or ""),
        evidence=list(metadata.get("evidence") or []),
        playbook=list(metadata.get("playbook") or []),
    )
    if topic_context.owner:
        click.echo(f"Topic owner from synthesis frontmatter: {topic_context.owner}")
    else:
        click.echo(
            "No owner in the synthesis frontmatter — the draft will use "
            "[Responsible Team]. Re-run `synthesize --topic-name` to record one."
        )

    # The class the synthesis was drawn from, checked before any provider is
    # built. `classify_path` would call this file the organization's own —
    # it is a file under output/ — and that is exactly how a restatement of
    # HITRUST requirement text used to reach a hosted model one command after
    # `synthesize` had refused to send the HITRUST text itself.
    from policyforge.llm import boundary

    recorded = metadata.get("content_class")
    if recorded is not None:
        klass = str(recorded).strip().lower()
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
    else:
        # Written before the class travelled. Classified as it always was,
        # which is the most this file can say about itself.
        content = boundary.classify_path(synthesis_path, config)
    decision = boundary.check(content, boundary.classify_provider(config.get("llm") or {}), config)
    if not decision.allowed:
        raise click.ClickException(
            f"Refusing to draft from {synthesis_path} with the configured provider.\n"
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

    provider = get_provider(config)

    from policyforge.content.tree import TIER_DIRECTORY
    from policyforge.llm import ledger

    # Every call the drafting makes is attributed to this document, so the
    # version-history stamp below names the model that actually wrote it
    # rather than the one config happened to hold. Those differ whenever a
    # cascade escalates, which is exactly when the difference matters.
    # The directory is the one `check` reads this tier from (#411):
    # `f"{tier}s"` wrote every Policy to `policys/`, which no reader knows.
    out_path = out or Path("output") / TIER_DIRECTORY[tier] / synthesis_path.name
    slug = f"{tier}/{out_path.stem}"

    # The class goes on the scope as well as through the check above, so each
    # call's ledger record says what it carried and the provenance stamped
    # into the version history says what the document was drawn from.
    with (
        ledger.run(report=click.echo),  # #379: what the run cost, said once
        ledger.about(slug, site="generate", content_class=content_class) as scope,
    ):
        if tier in ("policy", "procedure"):
            if standard_path is None:
                raise click.UsageError(
                    f"--standard is required when --tier {tier}, so the {tier.capitalize()} "
                    "can reference its Standard document by name."
                )
            standard_title = extract_title(standard_path.read_text(encoding="utf-8"))
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

    substituted = apply_substitutions(document, profile)
    document = substituted.text
    if substituted.filled:
        pairs = sorted({f"{label} -> {value}" for label, value in substituted.filled})
        click.echo(f"Filled {len(substituted.filled)} role placeholder(s): " + ", ".join(pairs))
    if substituted.outstanding:
        click.echo(
            f"{len(substituted.outstanding)} role(s) left as placeholders: "
            + ", ".join(substituted.outstanding)
        )
        click.echo("  Add them under `org.vendors` or `org.teams`, then regenerate.")

    # Stamped into the document, not just into the gitignored local history.
    # The question "which documents did that model touch" has to be
    # answerable from a checkout by someone who was not here — see
    # content/provenance.py. Written before the quality check so that what
    # mdformat judges is the file that ships.
    from policyforge.content.provenance import stamp_document

    # A stamp with no models asserts nothing. That happens when the ledger
    # saw no calls — a provider injected directly, or one built outside
    # `get_provider` — and writing `models: []` into a governance document
    # would be noise claiming to be provenance. Absence already means "not
    # known", which is the honest record here.
    provenance = scope.provenance()
    stamped = (
        stamp_document(document + "\n", provenance) if provenance.get("models") else document + "\n"
    )
    written = write_markdown(stamped, output_dir=out_path.parent, filename=out_path.name)
    if not check_markdown_quality(written):
        click.echo(
            f"WARNING: {written} did not pass the mdformat quality check — review before shipping."
        )
    click.echo(f"Drafted {tier} -> {written}")

    from policyforge.history.version_store import record_version

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
            "synthesis_source": str(synthesis_path),
            **scope.provenance(),
        },
    )
    if scope.models:
        click.echo(f"Drafted by: {', '.join(scope.models)} ({len(scope.records)} call(s))")
    if record is None:
        click.echo(
            f"No content change since the last recorded version of {slug!r} — history unchanged."
        )
    else:
        click.echo(
            f"Recorded {slug!r} v{record.version} in {history_dir} "
            f"(+{record.lines_added}/-{record.lines_removed} lines)."
        )

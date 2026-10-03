"""Analyses of the programme itself: ownership, coverage, parameters, drift."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import (
    _DEFAULT_HISTORY_DIR,
    _checked_slug,
    _content_dir,
    load_catalogs,
    load_config_or_empty,
)


@cli.command("map")
@click.option(
    "--controls",
    "controls_paths",
    multiple=True,
    default=(Path("data/frameworks/nist-800-53-r5/controls.json"),),
    type=click.Path(exists=True, path_type=Path),
    help="Path to a controls.json. Repeatable, and normally repeated: a "
    "cross-framework crosswalk needs every framework loaded together, so passing "
    "only the NIST file produces a crosswalk with nothing to cross-reference.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/crosswalk.json"),
    type=click.Path(path_type=Path),
    help="Where to write the built crosswalk.",
)
def map_cmd(controls_paths, out: Path):
    """Build the NIST-anchored cross-framework crosswalk."""
    import json

    from policyforge.mapping.crosswalk import build_crosswalk

    controls = load_catalogs(controls_paths)

    crosswalk = build_crosswalk(controls)
    out.parent.mkdir(parents=True, exist_ok=True)
    from policyforge.textfile import write_text_lf

    # LF on every platform, so a rebuilt crosswalk.json diffs only where the
    # crosswalk changed.
    write_text_lf(out, json.dumps(crosswalk, indent=2))
    # Which overlays this crosswalk reflects, by content, so `synthesize` can
    # tell a crosswalk built before an overlay changed from a current one.
    from policyforge.crosswalk.overlay import overlay_digests, provenance_path

    write_text_lf(
        provenance_path(out), json.dumps({"overlays": overlay_digests()}, indent=2, sort_keys=True)
    )

    frameworks = sorted({f for entry in crosswalk.values() for f in entry})
    click.echo(f"Built crosswalk for {len(crosswalk)} NIST controls -> {out}")
    if frameworks:
        click.echo(f"  Frameworks mapped: {', '.join(frameworks)}")
    else:
        click.echo(
            "  No cross-framework mappings found — pass every framework's "
            "controls.json with repeated --controls so they can be crossed."
        )


def _topics_and_controls(topics_path: Path, controls_paths):
    """The registry and the catalogs, split into anchorable and reachable.

    The registry anchors on NIST ids, so every view over it has to know
    which half of a mixed catalog set can be anchored and which half is only
    reachable through the crosswalk. Shared rather than repeated because
    getting that split wrong makes a HIPAA requirement look anchorable.
    """
    from policyforge.mapping.crosswalk import anchors_a_topic
    from policyforge.topics.registry import load_topics

    topics = load_topics(topics_path)
    controls = load_catalogs(controls_paths)
    nist = [c for c in controls if anchors_a_topic(c.framework)]
    other = [c for c in controls if not anchors_a_topic(c.framework)]
    return topics, controls, nist, other


@cli.command("bundle")
@click.argument("owner")
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(exists=True, path_type=Path),
    help="Topic registry (default: config/topics.yaml).",
)
@click.option(
    "--controls",
    "controls_paths",
    required=True,
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a controls.json. Repeatable.",
)
def bundle_cmd(owner: str, topics_path: Path, controls_paths):
    """Everything one team answers for: topics, requirements, documents, cadence.

    The question a team lead asks, which `coverage` answers only by being
    read whole and filtered by eye. Set arithmetic over the registry - no
    model involved, and the answer is exactly as good as the registry is.
    """
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.bundles import team_bundle

    topics, controls, nist, _ = _topics_and_controls(topics_path, controls_paths)
    click.echo(team_bundle(topics, nist, owner, crosswalk=build_crosswalk(controls)).render())


@cli.command("addresses")
@click.argument("requirement")
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(exists=True, path_type=Path),
    help="Topic registry (default: config/topics.yaml).",
)
@click.option(
    "--controls",
    "controls_paths",
    required=True,
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a controls.json. Repeatable - pass the framework the "
    "requirement belongs to as well as the NIST catalog.",
)
def addresses_cmd(requirement: str, topics_path: Path, controls_paths):
    """Who answers for one requirement, and which document says so.

    The assessor's direction of travel. Name any requirement - NIST, HIPAA,
    HITRUST - and this resolves it through the crosswalk to the topics the
    registry anchors, then names the owner and the pages.

    Every claim says how it was reached: anchored directly, inherited from an
    anchored parent (a control, or an AI RMF category), or reached through a
    published crosswalk. The last is
    the weakest and is labelled as such, because a mapping is not evidence
    that anybody wrote the requirement down.
    """
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.bundles import requirement_view

    topics, controls, nist, other = _topics_and_controls(topics_path, controls_paths)
    click.echo(
        requirement_view(
            topics,
            nist,
            requirement,
            other_controls=other,
            crosswalk=build_crosswalk(controls),
        ).render()
    )


@cli.command("coverage")
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(exists=True, path_type=Path),
    help="Topic registry (default: config/topics.yaml). Copy config/topics.example.yaml to start.",
)
@click.option(
    "--controls",
    "controls_paths",
    required=True,
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a controls.json. Repeatable - pass the NIST 800-53 file plus any "
    "other framework you want reachability reported for.",
)
@click.option(
    "--baseline",
    type=click.Choice(["low", "moderate", "high"], case_sensitive=False),
    default=None,
    help="Limit the scope to one NIST baseline. 'Orphaned' only means anything "
    "relative to a defined scope, so this is usually what you want.",
)
@click.option(
    "--show-all", is_flag=True, help="List every orphaned control, not just the first 40."
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero if anything is orphaned, contested, or anchored to an unknown "
    "control - for use as a CI gate.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON instead of a text summary.",
)
def coverage_cmd(
    topics_path: Path,
    controls_paths,
    baseline: str | None,
    show_all: bool,
    strict: bool,
    as_json: bool,
):
    """Report which in-scope controls no topic owns, and which two topics claim.

    Orphaned controls mean nobody is doing the work. Contested controls are
    worse: it looks covered while each owner assumes the other has it. Both
    are pure set arithmetic over the topic registry - no LLM involved. See
    README's "One topic, one team".
    """
    import dataclasses
    import json as json_mod

    from policyforge.mapping.crosswalk import TOPIC_ANCHORS, anchors_a_topic, build_crosswalk
    from policyforge.ssp.workbook import select_for_baseline
    from policyforge.topics.coverage import (
        analyze_coverage,
        format_report,
        split_by_adoption,
        unadopted_note,
    )
    from policyforge.topics.registry import load_topics

    topics = load_topics(topics_path)

    all_controls = load_catalogs(controls_paths)

    # In scope is what the registry anchors, not what it could anchor --
    # see `adopted_frameworks`. `documents.py` deliberately keeps the
    # wider set, because generation needs a control to be AVAILABLE,
    # which is a different question from whether it is in a denominator.
    nist_controls, unadopted, _unused = split_by_adoption(topics, all_controls)
    if not nist_controls:
        raise click.UsageError(
            # Derived from TOPIC_ANCHORS rather than naming 800-53, so it
            # stays true when the set widens. A false error message is worse
            # than a missing one: it is confident, it gets quoted back, and
            # it sends the reader to fix the wrong thing -- the same family
            # as a remedy command that cannot run.
            "None of the --controls files contain a catalog a topic can anchor. "
            f"Topics anchor identifiers from: {', '.join(sorted(TOPIC_ANCHORS))}. "
            "At least one is required; run `policyforge etl-oscal` for 800-53."
        )
    other_controls = [c for c in all_controls if not anchors_a_topic(c.framework)]

    scoped = nist_controls
    scope = "all controls"
    if baseline:
        scoped = select_for_baseline(nist_controls, baseline)
        scope = f"{baseline.capitalize()} baseline"
        if not scoped:
            raise click.UsageError(
                f"No controls matched the {baseline!r} baseline. Was the catalog loaded "
                "with baseline profiles (see `policyforge etl-oscal`)?"
            )

    from policyforge.crosswalk.overlay import load_overlays, relationships_for
    from policyforge.topics.coverage import ScopingError

    try:
        report = analyze_coverage(
            topics,
            scoped,
            catalog=nist_controls,
            scope=scope,
            other_controls=other_controls,
            crosswalk=build_crosswalk(all_controls) if other_controls else None,
            # Already read by load_catalogs above, which refuses an unreadable one.
            # Declared relationships (#408) first, then the overlays' accepted rows.
            relationships=relationships_for(all_controls, load_overlays()),
        )
    except ScopingError as exc:
        # The user's own config, refused with its reason, not a traceback
        # (1d on #461).
        raise click.UsageError(str(exc)) from exc

    if as_json:
        click.echo(json_mod.dumps(dataclasses.asdict(report), indent=2))
    else:
        click.echo(format_report(report, show_all=show_all))
        # Named, not silently absent. A catalog excluded from the
        # denominator because nobody anchors it is a fact the reader should
        # be able to act on, not a gap in the report.
        for line in unadopted_note(unadopted):
            click.echo(line)

    if strict and not report.is_clean:
        raise SystemExit(1)


@cli.command("history")
@click.option(
    "--tier",
    # "confluence" is the stream `edit-confluence`/`edit-topic` write into,
    # keyed by page-title slug rather than document name. Without it here,
    # those commands recorded history this command could not display.
    type=click.Choice(["standard", "policy", "procedure", "confluence"]),
    required=True,
)
@click.option(
    "--name",
    required=True,
    help="Document slug/filename stem - the stem of the file `policyforge generate` "
    "wrote via --out (e.g. 'authenticator-mgmt' for authenticator-mgmt.md), the "
    "--name given to `import-confluence`, or for --tier confluence, the page title "
    "slugified (e.g. 'access-control-standard').",
)
@click.option(
    "--history-dir",
    default=_DEFAULT_HISTORY_DIR,
    type=click.Path(path_type=Path),
    help="Where local version history is recorded (default: output/.history).",
)
@click.option(
    "--diff",
    "diff_range",
    default=None,
    help="Show a unified diff between two versions instead of listing them: 'N:M' for "
    "specific version numbers (e.g. '2:3'), or 'latest' for the two most recent versions.",
)
def history_cmd(tier: str, name: str, history_dir: Path, diff_range: str | None):
    """List (or diff) the locally recorded version history for one document."""
    from policyforge.history.version_store import diff_versions, load_history

    slug = f"{tier}/{_checked_slug(name)}"
    records = load_history(history_dir, slug)
    if not records:
        click.echo(f"No recorded history for {slug!r} in {history_dir}.")
        return

    if diff_range is not None:
        if diff_range == "latest":
            if len(records) < 2:
                raise click.UsageError(f"{slug!r} only has one recorded version — nothing to diff.")
            v1, v2 = records[-2].version, records[-1].version
        else:
            try:
                v1_str, v2_str = diff_range.split(":", 1)
                v1, v2 = int(v1_str), int(v2_str)
            except ValueError as exc:
                raise click.UsageError(
                    f"--diff must be 'N:M' or 'latest', got {diff_range!r}."
                ) from exc
        click.echo(diff_versions(history_dir, slug, v1, v2))
        return

    for record in records:
        click.echo(
            f"v{record.version}  {record.timestamp}  {record.source:<22} "
            f"+{record.lines_added}/-{record.lines_removed}  {record.content_hash}"
        )
        # An edit's plan is the "why" behind the version. Storing it without
        # ever showing it would leave the changelog answering what changed but
        # not what was asked for, or what was deliberately not done.
        metadata = record.metadata or {}
        plan = metadata.get("plan")
        if plan:
            click.echo(f"        asked: {plan.get('instruction', '')}")
            for step in plan.get("steps") or []:
                click.echo(
                    f"        - [{step.get('kind')}] {step.get('target')}: {step.get('summary')}"
                )
            for risk in plan.get("risks") or []:
                click.echo(f"        ! flagged: {risk}")
            for skipped in plan.get("out_of_scope") or []:
                click.echo(f"        ~ not done: {skipped}")
        elif metadata.get("instruction"):
            click.echo(f"        asked: {metadata['instruction']}")


@cli.command("drift")
@click.option(
    "--controls",
    "controls_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="The updated catalog. Compared against its committed version unless --old says otherwise.",
)
@click.option(
    "--old",
    "old_path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Explicit previous catalog. Without it, the version git has.",
)
@click.option(
    "--revision",
    default="HEAD",
    show_default=True,
    help="Git revision to compare against when --old is not given.",
)
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(path_type=Path),
    help="Registry, to say which topics a change reaches.",
)
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Content tree, to say which documents cite a changed control.",
)
@click.option(
    "--parameters",
    "parameters_path",
    default=Path("config/parameters.yaml"),
    type=click.Path(path_type=Path),
    help="Ledger, to say which recorded decisions a change reaches.",
)
@click.option("--detail", is_flag=True, help="Show the changed lines of each control statement.")
@click.option(
    "--fail-on-change",
    is_flag=True,
    help="Exit non-zero when anything substantive changed. For a scheduled "
    "job that should open an issue rather than pass quietly.",
)
def drift_cmd(
    controls_path: Path,
    old_path: Path | None,
    revision: str,
    topics_path: Path,
    content_dir: Path | None,
    parameters_path: Path,
    detail: bool,
    fail_on_change: bool,
):
    """Report what a framework update changed, and what it reaches.

    A catalog bump touches a few dozen controls out of a thousand, and behind
    those sit a handful of your topics and a smaller handful of your
    documents. Everything else is unaffected and should stay unread.

    Run the ETL, then run this - the ETL overwrites the catalog in place and
    git is still holding the version you had, so no snapshot is needed:

        policyforge etl-oscal
        policyforge drift --controls data/frameworks/nist-800-53-r5/controls.json
    """
    from policyforge.frameworks.drift import analyze_drift, load_previous
    from policyforge.ingest.schema import load_controls

    new_controls = load_controls(controls_path)

    if old_path is not None:
        old_controls = load_controls(old_path)
    else:
        old_controls = load_previous(controls_path, revision=revision)
        if old_controls is None:
            raise click.UsageError(
                f"Could not read {controls_path} at {revision} from git, so there is "
                "nothing to compare against. Pass --old with the previous catalog, or "
                "commit the current one first so the next update has a baseline."
            )

    topics = []
    if topics_path.exists():
        from policyforge.topics.registry import TopicRegistryError, load_topics

        try:
            topics = load_topics(topics_path)
        except TopicRegistryError as exc:
            click.echo(f"  (topic registry unreadable: {exc} — topics not assessed)")

    decisions = {}
    if parameters_path.exists():
        from policyforge.parameters.ledger import load_ledger

        decisions = load_ledger(parameters_path)

    root = _content_dir(content_dir) if content_dir is not None else None
    if root is None:
        default_root = _content_dir(None)
        root = default_root if default_root.exists() else None

    # The hub's crosswalk (80's ruling on #377): every catalog this
    # installation loads, so a change reaches documents citing another
    # catalog through that catalog's mapping to 800-53. Only what is loaded:
    # a catalog nobody configured reaches nothing. The diffed catalog comes
    # from its two versions, not from disk twice.
    from policyforge.frameworks.registry import discover
    from policyforge.mapping.crosswalk import build_crosswalk

    diffed = Path(controls_path).resolve()
    others = [
        f.controls_path
        for f in discover(load_config_or_empty())
        if f.has_controls and Path(f.controls_path).resolve() != diffed
    ]
    loaded = [*load_catalogs(others), *old_controls, *new_controls]
    crosswalk = build_crosswalk(loaded)
    # Every id each loaded catalog has: what "this citation resolves" asks.
    from policyforge.mapping.crosswalk import normalize_framework
    from policyforge.topics.anchoring import families_for

    catalogs: dict[str, set[str]] = {}
    for control in loaded:
        ids = catalogs.setdefault(normalize_framework(control.framework), set())
        ids.add(control.control_id)
        ids.update(e.enhancement_id for e in control.enhancements)

    report = analyze_drift(
        old_controls,
        new_controls,
        topics=topics,
        content_root=root,
        decisions=decisions,
        crosswalk=crosswalk,
        catalogs=catalogs,
        # Each regulatory catalog's declared unit (#423), over what is loaded.
        families=families_for(loaded),
    )
    click.echo(report.format_report(detail=detail))

    if root is None:
        click.echo("")
        click.echo(
            "  (no content tree found, so no documents were assessed — pass "
            "--content-dir to include them)"
        )

    if fail_on_change and report.needs_review:
        raise SystemExit(1)


@cli.command("parameters")
@click.option(
    "--controls",
    "controls_paths",
    multiple=True,
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Framework controls.json to read parameters from. Repeatable.",
)
@click.option(
    "--ledger",
    "ledger_path",
    default=Path("config/parameters.yaml"),
    type=click.Path(path_type=Path),
    help="Where decisions are recorded.",
)
@click.option(
    "--baseline",
    type=click.Choice(["low", "moderate", "high"]),
    default=None,
    help="Only controls in this baseline. A thousand parameters is not a "
    "to-do list; the ones you must answer for are.",
)
@click.option(
    "--topics",
    "topics_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Only controls your topic registry anchors - the narrowest useful scope.",
)
@click.option(
    "--group",
    is_flag=True,
    help="Summarise by the kind of value being decided (frequency, personnel "
    "or roles) rather than listing every parameter.",
)
@click.option(
    "--init",
    "init",
    is_flag=True,
    help="Write a ledger covering every in-scope parameter, preserving any "
    "decisions already recorded.",
)
def parameters_cmd(
    controls_paths,
    ledger_path: Path,
    baseline: str | None,
    topics_path: Path | None,
    group: bool,
    init: bool,
):
    """Record one decided value per organization-defined parameter.

    SP 800-53 does not say how often to review accounts - it says
    [Assignment: organization-defined frequency] and leaves it to you, 1,210
    times across the catalog. Today those get decided implicitly inside
    generated prose, by a model with no memory of what it chose for the
    neighbouring control, so the Access Control Standard says quarterly and
    the SSP says annually and nobody decided anything.

    A value recorded here is substituted into the control text before
    synthesis, so every document drawn from that control agrees, and the
    reasoning stays next to the value where an assessor can find it.
    """
    from policyforge.parameters.ledger import build_report, load_ledger, render_ledger

    controls = load_catalogs(controls_paths)

    if baseline:
        controls = [c for c in controls if c.baseline and baseline in c.baseline.lower()]
    if topics_path is not None:
        from policyforge.topics.registry import load_topics

        anchored = {
            control_id.upper()
            for topic in load_topics(topics_path)
            for control_id in topic.nist_controls
        }
        # The one rule for what a topic reaches (#377), over the catalogs
        # loaded here: an anchor claims its own catalog's children (AC-2 brings
        # AC-2(1)), and a catalog topics do not anchor is reached through its
        # crosswalk to 800-53 (80's ruling (B) on #377). This used a bare
        # `split("(")`, which reached FedRAMP by id shape and 800-171 not at
        # all; and coverage's old `parent_of`, which read no framework, claimed
        # 72 Playbook rows when swapped in (#318). A topic owns nothing in the
        # Playbook, and `topic_keys` reads the framework, so it claims none.
        from policyforge.mapping.crosswalk import build_crosswalk
        from policyforge.topics.anchoring import topic_keys

        crosswalk = build_crosswalk(controls)
        controls = [
            c
            for c in controls
            if {k.upper() for k in topic_keys(c.control_id, c.framework, crosswalk)} & anchored
        ]

    if not controls:
        raise click.UsageError("No controls in scope — check --baseline and --topics.")

    decisions = load_ledger(ledger_path)
    report = build_report(controls, decisions)

    if init:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(render_ledger(report.parameters, decisions), encoding="utf-8")
        click.echo(
            f"Wrote {ledger_path} with {len(report.parameters)} parameter(s); "
            f"{len(report.decided)} decision(s) preserved."
        )
        click.echo("")

    click.echo(report.format_report(group=group))
    if report.undecided and not init:
        click.echo("")
        click.echo(
            f"Run with --init to scaffold {ledger_path} with every one of them, "
            "then fill in the values you can defend."
        )


@cli.command("frameworks")
def frameworks_cmd():
    """List the framework catalogs on disk, and their licence position.

    A catalog is public domain or it is licensed, and that decides which
    repository may hold a copy of it. This repository may hold only the
    former; your own private repository can very often hold both, because
    your MyCSF or GovRAMP licence permits internal use.

    Licensed content committed to a repository that has not declared the
    right to hold it is reported as an error - set
    `frameworks.allow_licensed_in_repo: true` in config.yaml if your licence
    permits yours to carry it.
    """
    from policyforge.frameworks.registry import check_licences

    config = load_config_or_empty()

    report = check_licences(config)
    if not report.frameworks:
        # Non-zero, and naming `init` first: from an installed package (Homebrew,
        # pipx) this is what a new user sees before laying out a project, and a
        # listing that finds nothing is not a success a script should pass on.
        click.echo(
            "No frameworks found in this directory. Every command reads "
            "data/frameworks/ relative to where it runs. Start a project with "
            "`policyforge init`, fetch 800-53 with `policyforge etl-oscal`, or point "
            "`frameworks.search_paths` at your own catalogs.",
            err=True,
        )
        raise SystemExit(1)

    click.echo(report.format_report())
    # Where declarations are inspected is where their prose collisions are
    # shown (80 on #347): facts for the user to judge, never a failure.
    from policyforge.frameworks.registry import key_collisions

    facts = key_collisions(config)
    if facts:
        click.echo("")
        click.echo("Framework keys worth a look:")
        for fact in facts:
            click.echo(f"  {fact}")
    if report.allowed:
        click.echo("")
        click.echo(
            "This repository declares `allow_licensed_in_repo: true`, so committing "
            "licensed catalogs here is permitted by your own configuration. That is a "
            "statement about your licence, not a check of it."
        )
    if not report.ok:
        raise SystemExit(1)


@cli.command("roles")
@click.option(
    "--kind",
    type=click.Choice(["vendors", "teams", "all"]),
    default="all",
    show_default=True,
    help="Which taxonomy to list.",
)
def roles_cmd(kind: str):
    """List the tool and team roles config can assign.

    These keys go under `org.vendors` and `org.teams` in config.yaml, and
    naming a role is what makes substitution deterministic: the generator is
    told what a tool is *for* rather than inferring it from a product name,
    and every placeholder for a filled role is then replaced exactly.

    The lists are not exhaustive. They cover what NIST 800-53 and HIPAA
    Security Rule procedures actually reference; a tool fitting none of them
    can still be passed as a plain list.
    """
    from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES

    wanted = {"vendors": ("Tool",), "teams": ("Team",), "all": ("Tool", "Team")}[kind]
    for title, roles in (("Tool", VENDOR_ROLES), ("Team", TEAM_ROLES)):
        if title not in wanted:
            continue
        click.echo(f"{title} roles ({len(roles)}):")
        width = max(len(key) for key in roles)
        for key, role in roles.items():
            families = f"  [{', '.join(role.families)}]" if role.families else ""
            click.echo(f"  {key.ljust(width)}  {role.placeholder}{families}")
            click.echo(f"  {' ' * width}  {role.description}")
        click.echo("")

    click.echo("Assign them like this:")
    click.echo("    org:")
    click.echo("      vendors:")
    click.echo("        identity_provider: Okta")
    click.echo("        ticketing: Jira")
    click.echo("      teams:")
    click.echo("        identity_access: IAM Engineering")


@cli.command("satisfies")
@click.option(
    "--document",
    "document_paths",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="A single markdown document, by path. Repeatable.",
)
@click.option(
    "--topic",
    "topic_names",
    multiple=True,
    help="Every document of a topic. Repeatable. Matched on a document's frontmatter "
    "topic or its filename slug, since `generate` names the file for the slug but "
    "writes no topic - which is the state a freshly generated tree is in.",
)
@click.option("--all", "every", is_flag=True, help="Every document in the content tree.")
@click.option(
    "--content-dir",
    "content_override",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The markdown tree (default: config's zardoz.content_dir, else docs/).",
)
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(path_type=Path),
    help="Topic registry (default: config/topics.yaml). Read for a topic's anchors, so "
    "'anchored but cited nowhere' can be reported; the rest works without it.",
)
@click.option(
    "--controls",
    "controls_paths",
    required=True,
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a controls.json. Repeatable - pass the NIST catalog plus every "
    "framework you want the documents resolved against.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero if any document cites a requirement no loaded catalog has. "
    "A citation pointing nowhere is a defect in the document, so this is the CI gate.",
)
@click.option(
    "--require-reviewed",
    is_flag=True,
    help="Also exit non-zero if any mapping rests on an overlay entry nobody has "
    "reviewed. Separate from --strict: unreviewed is a state of the programme, not "
    "an error in the document.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the evidence as JSON instead of a text report.",
)
def satisfies_cmd(
    document_paths,
    topic_names,
    every: bool,
    content_override: Path | None,
    topics_path: Path,
    controls_paths,
    strict: bool,
    require_reviewed: bool,
    as_json: bool,
):
    """What a published document can be shown against, and how strong the evidence is.

    The assessor's direction of travel, from the page rather than the
    requirement: name a document and this reports the requirements it cites,
    the ones those citations reach through the crosswalk, and - separately -
    the controls its topic claims that nothing in the topic actually says.

    The citations in the text are the evidence. A topic anchor is an
    intention, so the two are never added together. Every crosswalked
    mapping says where it came from: the published crosswalk, an overlay row
    somebody reviewed, or an overlay row nobody has. The last are shown and
    marked rather than dropped. No model call and no network.

    The crosswalk is anchored on NIST 800-53 and is walked from the NIST
    requirements a document cites, so a document citing none reaches nothing
    through it - and the report says so rather than printing an empty
    section, which on this command would read as an absence of coverage.

    Two resolution rules worth knowing, because the counts look wrong
    without them. Citing an enhancement counts as citing its control, so a
    topic that cites AC-2(3) is not reported as never mentioning AC-2, and
    one citing Govern 1.1 is not reported as never mentioning Govern 1 - the
    same `parent_of` reading `coverage` uses, and `drift` for the catalogs
    topics anchor. And a topic's anchors are
    answered by its documents together rather than one file at a time, so a
    Policy is not reported as missing anchors its own Procedure cites; the
    report names how much it searched. Under --document, one file is all
    there is to search, and it says so.
    """
    import json as json_mod

    from policyforge.content.tree import load_content_tree
    from policyforge.crosswalk.overlay import accepted_rows, load_overlays
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.registry import load_topics
    from policyforge.topics.satisfies import as_records, build_report, format_report

    if sum(bool(x) for x in (document_paths, topic_names, every)) != 1:
        raise click.UsageError(
            "Choose exactly one of --document, --topic or --all. They are different "
            "questions: one page, one topic's pages, or the whole tree."
        )

    root = _content_dir(content_override)
    if not root.exists():
        raise click.UsageError(
            f"No content tree at {root}. Generate documents first, or pass --content-dir."
        )
    documents, problems = load_content_tree(root)
    for problem in problems:
        click.echo(f"  ! {problem}", err=True)

    if document_paths:
        wanted = {p.resolve() for p in document_paths}
        selected = [d for d in documents if d.path.resolve() in wanted]
        missing = wanted - {d.path.resolve() for d in selected}
        if missing:
            raise click.UsageError(
                "These are not in the content tree at "
                f"{root}: {', '.join(sorted(str(m) for m in missing))}"
            )
    elif topic_names:
        from policyforge.zardoz.corpus import slugify

        # By frontmatter topic *or* by slug. `generate` writes a file named
        # for the topic's slug but puts no `topic:` in the frontmatter —
        # that is added later, when a document is published — so matching
        # the frontmatter alone would find nothing in a freshly generated
        # tree, which is exactly when this report is most worth running.
        wanted = {slugify(name) for name in topic_names}
        selected = [d for d in documents if slugify(d.topic) in wanted or slugify(d.slug) in wanted]
        if not selected:
            raise click.UsageError(
                f"No document in {root} belongs to {', '.join(topic_names)}. "
                "Documents are matched on their frontmatter topic or their filename slug."
            )
    else:
        selected = list(documents)
        if not selected:
            raise click.UsageError(f"No documents found in {root}.")

    topics = load_topics(topics_path) if topics_path.exists() else []
    controls = load_catalogs(controls_paths)

    evidences = build_report(
        selected,
        controls=controls,
        crosswalk=build_crosswalk(controls),
        # Read again rather than reused from load_catalogs, which applies them
        # and keeps no handle. Cheap, and the alternative is a second return
        # value on a function six commands call.
        provenance=accepted_rows(load_overlays()),
        topics=topics,
    )

    if as_json:
        click.echo(json_mod.dumps(as_records(evidences), indent=2))
    else:
        click.echo(format_report(evidences))

    unknown = sum(len(e.unknown) for e in evidences)
    unreviewed = sum(len(e.unreviewed) for e in evidences)
    if strict and unknown:
        click.echo(f"{unknown} citation(s) resolve to nothing in the loaded catalogs.", err=True)
        raise SystemExit(1)
    if require_reviewed and unreviewed:
        click.echo(f"{unreviewed} mapping(s) rest on an unreviewed overlay entry.", err=True)
        raise SystemExit(1)

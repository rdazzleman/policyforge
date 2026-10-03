"""The content tree and Confluence: export, edit, import, check, publish, pull."""

from __future__ import annotations

import shlex
from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import (
    _DEFAULT_HISTORY_DIR,
    _checked_slug,
    _content_dir,
    _zardoz_setting,
    get_provider,
    load_config,
    load_config_or_empty,
)
from policyforge.textfile import write_text_lf


def history_hint(*, tier: str, name: str, previous: int | str, current: int | str) -> str:
    """The `history` invocation that shows what changed, as printed.

    **Extracted so the quoting is reachable from a test.** It was inline
    in a `click.echo`, and reverting its `shlex.quote` left the suite
    green -- the site was covered by neither the publisher test, which
    reaches publishers, nor the source-level scan, whose population is
    backtick-quoted strings.

    A document name is user-authored: `Ryan's Access Policy` inside
    hand-written quotes produces a valid command naming a different
    document, silently. Same defect as a page title, different caller.

    **The versions arrive as ints** (`VersionRecord.version`) and
    `shlex.quote` takes only strings, so they are `str()`ed here. Without
    that, every import that differed from a recorded version crashed after
    it had written its file (#310). The first test of this helper passed
    `"v1"`, a value no caller sends.
    """
    # Converted before the f-string, so the quoted expressions keep the text
    # `test_printed_commands_run` pins them by.
    previous, current = str(previous), str(current)
    return (
        f"Run `policyforge history "
        f"--tier {shlex.quote(tier)} --name {shlex.quote(name)} "
        f"--diff {shlex.quote(previous)}:{shlex.quote(current)}` to see what changed."
    )


@cli.command("export-confluence")
@click.option(
    "--doc",
    "doc_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a generated markdown document (from `policyforge generate`).",
)
@click.option("--space", required=True, help="Confluence space key to publish into.")
@click.option("--title", required=True, help="Page title.")
@click.option(
    "--host",
    required=True,
    help="Confluence base URL, e.g. https://yourorg.atlassian.net/wiki.",
)
@click.option(
    "--parent-id", default=None, help="Parent page ID, if nesting under an existing page."
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the converted Confluence storage format instead of publishing.",
)
def export_confluence_cmd(
    doc_path: Path, space: str, title: str, host: str, parent_id: str | None, dry_run: bool
):
    """Convert a generated markdown document to Confluence storage format and publish it."""
    from policyforge.export.confluence_exporter import export_to_confluence, markdown_to_confluence

    markdown_text = doc_path.read_text(encoding="utf-8")

    if dry_run:
        click.echo(markdown_to_confluence(markdown_text))
        return

    url = export_to_confluence(
        markdown_text, space=space, title=title, host=host, parent_id=parent_id
    )
    click.echo(f"Published to Confluence -> {url}")


#: Diff lines shown in the terminal before pointing at the file. The cap is
#: for readability only — the whole diff is written to `<slug>.diff` next to
#: the revision, so nothing a reviewer needs is only ever off-screen.
_DIFF_LINES = 120


#: The words that differ between editing a wiki page and editing a file.
#: Everything else in the review — the plan, the diff, every check and every
#: warning — is the same code for both, which is the point: the file path is
#: not a lighter-weight copy of the wiki path that can drift.
_WIKI = {"original": "live page", "nothing": "publish", "before": "publishing"}


_TREE = {"original": "file", "nothing": "write", "before": "committing"}


def _refuse_reader_directed(targets, allow_reader_directed: bool) -> None:
    """Refuse, before any model call, a document addressed to the prompt.

    Shared by both edit destinations. A file in the content tree is not a
    safer input than a wiki page: `pull` writes page bodies into it verbatim,
    so a line planted on a page arrives in the file on the next pull.
    """
    from policyforge.zardoz import injection

    # Anyone with edit rights on a wiki page can put a line in it addressed to
    # whoever reads the prompt, and this is the path that publishes back to
    # the live policy set. The fence means such a line is quoted rather than
    # obeyed; this refusal means a person gets told it is there. Checked
    # before the first LLM call for the same reason the macro check is.
    directed = [(t, injection.scan_document(t.title, t.original)) for t in targets]
    directed = [(t, findings) for t, findings in directed if findings]
    if directed and not allow_reader_directed:
        detail = "\n".join(
            f"  {t.label}:\n"
            + "\n".join(f"    {f}" for f in findings[:5])
            + (f"\n    ... {len(findings) - 5} more" if len(findings) > 5 else "")
            for t, findings in directed
        )
        raise click.UsageError(
            "These pages contain text addressed to the reader of a prompt rather "
            f"than to the organization:\n{detail}\n"
            "Each is a heuristic hit, and some are innocent — a runbook somebody "
            "pasted a chat transcript into reads the same way. Go and look at the "
            "page. Re-run with --allow-reader-directed once you have."
        )
    for target, findings in directed:
        click.echo(f"WARNING: {target.label} contains reader-directed text:")
        for finding in findings[:5]:
            click.echo(f"  {finding}")
        if len(findings) > 5:
            click.echo(f"  ... {len(findings) - 5} more")


def _review_edits(targets, instruction, provider, *, out_dir: Path, slugs: dict, words=None):
    """Plan, rewrite, show and check every target. Returns what is writable.

    Nothing is written to the destination here. What is written is the
    review artifact — the full diff, the revision and the plan — to
    `out_dir`, so a dry run leaves something to read after the terminal is
    closed.
    """
    import difflib
    import json

    from policyforge.edit.session import apply_targets, plan_targets

    words = words or _WIKI

    outcomes = plan_targets(targets, instruction, provider)
    for outcome in outcomes:
        click.echo("")
        click.echo("=" * 60)
        click.echo(outcome.plan.render())

    if all(o.plan.is_empty for o in outcomes):
        click.echo("\nNo edits proposed for any page. Nothing to apply.")
        return []

    from policyforge.edit.apply import EchoedFenceError

    # Refused before anything is written, to the wiki or to the tree: every
    # target is rewritten before any is published, so one refusal leaves the
    # whole set untouched.
    try:
        apply_targets(outcomes, provider)
    except EchoedFenceError as exc:
        raise click.ClickException(str(exc)) from exc
    out_dir.mkdir(parents=True, exist_ok=True)

    publishable = []
    for outcome in outcomes:
        if outcome.plan.is_empty:
            click.echo(f"\n{outcome.target.label}: no edits planned — leaving unchanged.")
            continue
        check = outcome.check
        slug = slugs[id(outcome.target)]
        click.echo("")
        click.echo("=" * 60)
        click.echo(f"{outcome.target.label} (+{check.lines_added}/-{check.lines_removed})")
        diff = list(
            difflib.unified_diff(
                outcome.target.original.splitlines(),
                outcome.revised.splitlines(),
                "before",
                "after",
                lineterm="",
                n=2,
            )
        )
        # The terminal shows a readable amount; the file holds all of it.
        # Truncating was the only copy of the diff a reviewer got, which put
        # a long insertion past the cut and out of sight.
        write_text_lf(out_dir / f"{slug}.diff", "\n".join(diff) + "\n")
        for line in diff[:_DIFF_LINES]:
            click.echo("  " + line)
        if len(diff) > _DIFF_LINES:
            click.echo(
                f"  ... {len(diff) - _DIFF_LINES} more diff lines — full diff: "
                f"{out_dir / f'{slug}.diff'}"
            )

        if check.unchanged:
            click.echo(
                f"  (rewrite is identical to the {words['original']} — "
                f"nothing to {words['nothing']})"
            )
            continue
        if not check.is_clean:
            if check.dropped_source_tags:
                click.echo(
                    "  WARNING: framework citations present before are missing after: "
                    + ", ".join(check.dropped_source_tags)
                )
            if check.removed_headings:
                click.echo(
                    "  WARNING: sections removed that the plan did not ask to remove: "
                    + ", ".join(check.removed_headings)
                )
            if check.dropped_source_tags or check.removed_headings:
                click.echo(f"  These are traceability losses — review before {words['before']}.")
            if check.changed_sections:
                click.echo(
                    "  WARNING: sections changed that no plan step named: "
                    + ", ".join(check.changed_sections)
                )
            if check.added_headings and not check.additions_were_planned:
                click.echo(
                    "  WARNING: sections added that the plan did not ask for: "
                    + ", ".join(check.added_headings)
                )
            if check.changed_sections or (
                check.added_headings and not check.additions_were_planned
            ):
                click.echo(
                    "  Text moved outside the approved plan. Read it in the diff "
                    f"before {words['before']} — this is what an instruction planted "
                    "in the page would look like."
                )

        write_text_lf(out_dir / f"{slug}.md", outcome.revised)
        # The plan is written next to the revision, so a dry run leaves a
        # reviewable artifact rather than only terminal output that scrolls away.
        write_text_lf(out_dir / f"{slug}.plan.json", json.dumps(outcome.plan.as_record(), indent=2))
        click.echo(f"  -> {out_dir / f'{slug}.md'} (plan: {slug}.plan.json)")
        publishable.append(outcome)

    if not publishable:
        click.echo(f"\nNothing to {words['nothing']}.")
    return publishable


def _confirm_edits(publishable, *, do_apply: bool, yes: bool, dry_run: str, prompt: str) -> bool:
    """True when the edits should be written; False for a dry run.

    `--yes` is for the routine case. A check that came back dirty is the case
    it is not for: something changed that nobody planned, and the whole point
    of the check is that a person sees it before it lands.
    """
    if not do_apply:
        click.echo(dry_run)
        return False

    unreviewed = [o for o in publishable if not o.check.is_clean]
    if unreviewed and yes:
        click.echo(
            "\n--yes does not cover these pages — their checks found changes the "
            "plan did not call for:"
        )
        for outcome in unreviewed:
            click.echo(f"  {outcome.target.label}")
    if not yes or unreviewed:
        names = ", ".join(o.target.label for o in publishable)
        click.confirm(f"{prompt} {names}?", abort=True)
    return True


def _edit_run(
    *,
    targets,
    instruction: str,
    host: str,
    do_apply: bool,
    yes: bool,
    allow_macros: bool,
    allow_reader_directed: bool = False,
    out_dir: Path,
    history_dir: Path,
    config: dict,
):
    """Shared body of `edit-confluence` and `edit-topic`.

    Fetch everything, refuse anything unsafe, plan, rewrite, show, then
    publish only on explicit approval. Nothing is written back until every
    page has been planned and rewritten, so a failure part-way through leaves
    the whole set untouched.

    "Unsafe" covers two things here. A page using macros this tool cannot
    round-trip would be damaged by editing it at all. A page containing text
    addressed to the model rather than to the organization is the other: the
    fence in `edit.fencing` is what holds on that one, and this refusal is
    the part that tells somebody to go and look at the page.
    """
    import re

    from policyforge.edit.session import fetch_targets
    from policyforge.export.confluence_exporter import ConcurrentEditError, update_page_body
    from policyforge.history.version_store import record_version

    provider = get_provider(config)
    model = (config.get("llm") or {}).get("model", "")

    fetch_targets(targets, host=host)
    for target in targets:
        click.echo(f"Fetched {target.label!r} (version {target.version}) — {target.webui_url}")

    # storage -> markdown -> storage is lossless only for what this project's
    # own exporter emits. Check every page before spending anything on the
    # LLM, so an unsafe page in a set fails the run up front rather than after
    # the other pages have already been rewritten.
    unsafe = [t for t in targets if t.unsupported_macros]
    if unsafe and not allow_macros:
        detail = "\n".join(f"  {t.label}: {', '.join(t.unsupported_macros)}" for t in unsafe)
        raise click.UsageError(
            "These pages use Confluence macros this tool cannot round-trip:\n"
            f"{detail}\nEditing them would flatten or drop those macros. Edit in "
            "Confluence directly, or re-run with --allow-macros if you have checked "
            "that losing them is acceptable."
        )
    for target in unsafe:
        click.echo(
            f"WARNING: {target.label} has unsupported macros: "
            f"{', '.join(target.unsupported_macros)}"
        )

    _refuse_reader_directed(targets, allow_reader_directed)

    slugs = {id(t): re.sub(r"[^a-z0-9]+", "-", t.title.lower()).strip("-") for t in targets}

    # Record the pre-edit state before the first LLM call, so there is a local
    # copy to restore from even if the run is abandoned partway.
    for target in targets:
        record_version(
            history_dir,
            f"confluence/{slugs[id(target)]}",
            target.original,
            source="confluence-edit-before",
            metadata={
                "space": target.space,
                "title": target.title,
                "tier": target.tier,
                "page_version": target.version,
            },
        )

    publishable = _review_edits(
        targets, instruction, provider, out_dir=out_dir, slugs=slugs, words=_WIKI
    )
    if not publishable:
        return

    if not _confirm_edits(
        publishable,
        do_apply=do_apply,
        yes=yes,
        dry_run="\nDry run — Confluence unchanged. Re-run with --apply to publish "
        f"{len(publishable)} page(s).",
        prompt="Publish edits to",
    ):
        return

    published, failed = [], []
    for outcome in publishable:
        target = outcome.target
        try:
            url = update_page_body(
                outcome.revised,
                page_id=target.page_id,
                title=target.title,
                space=target.space,
                host=host,
                expected_version=target.version,
            )
        except ConcurrentEditError as exc:
            failed.append((target, str(exc)))
            continue

        record_version(
            history_dir,
            f"confluence/{slugs[id(target)]}",
            outcome.revised,
            source="confluence-edit-after",
            metadata={
                "space": target.space,
                "title": target.title,
                "tier": target.tier,
                "page_version": target.version + 1,
                "model": model,
                "plan": outcome.plan.as_record(),
            },
        )
        published.append((target, url))
        click.echo(f"Published {target.label} version {target.version + 1} -> {url}")

    if failed:
        click.echo("")
        for target, message in failed:
            click.echo(f"FAILED {target.label}: {message}")
        if published:
            click.echo(
                f"\n{len(published)} page(s) were published before this failed. The set is "
                "now partly updated — re-run for the remaining pages once the conflict "
                "is resolved."
            )
        raise SystemExit(1)


@cli.command("edit-confluence")
@click.option("--instruction", required=True, help="What to change, in plain language.")
@click.option("--space", required=True, help="Confluence space key.")
@click.option("--title", required=True, help="Exact page title to edit.")
@click.option(
    "--host", required=True, help="Confluence base URL, e.g. https://x.atlassian.net/wiki."
)
@click.option(
    "--tier",
    type=click.Choice(["policy", "standard", "procedure"]),
    default=None,
    help="Which document tier this page is. Tells the planner what altitude to "
    "edit at - a threshold change belongs in a Standard, not a Policy.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Actually publish the edit. Without this the command plans, rewrites and "
    "shows you the diff, but changes nothing in Confluence.",
)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt before publishing.")
@click.option(
    "--allow-macros",
    is_flag=True,
    help="Proceed even though the page uses Confluence macros this tool cannot "
    "round-trip. They will be degraded or lost. Read the warning first.",
)
@click.option(
    "--allow-reader-directed",
    is_flag=True,
    help="Proceed even though the page contains text addressed to the reader of a "
    "prompt rather than to the organization. Look at the page first.",
)
@click.option(
    "--out-dir",
    default=Path("output/edits"),
    type=click.Path(path_type=Path),
    help="Where the revised markdown and its plan are written.",
)
@click.option(
    "--history-dir",
    default=_DEFAULT_HISTORY_DIR,
    type=click.Path(path_type=Path),
    help="Where local version history is recorded (default: output/.history).",
)
def edit_confluence_cmd(
    instruction: str,
    space: str,
    title: str,
    host: str,
    tier: str | None,
    do_apply: bool,
    yes: bool,
    allow_macros: bool,
    allow_reader_directed: bool,
    out_dir: Path,
    history_dir: Path,
):
    """Edit one live Confluence page from a plain-language instruction.

    Fetches the page, plans the edits, shows you the plan and the resulting
    diff, and publishes only if you pass --apply and confirm. The page's
    "before" state is recorded to local version history either way, and the
    plan is saved alongside the revision, so there is always something to
    diff against and a record of why it changed.
    """
    from policyforge.edit.session import EditTarget

    _edit_run(
        targets=[EditTarget(space=space, title=title, tier=tier or "")],
        instruction=instruction,
        host=host,
        do_apply=do_apply,
        yes=yes,
        allow_macros=allow_macros,
        allow_reader_directed=allow_reader_directed,
        out_dir=out_dir,
        history_dir=history_dir,
        config=load_config(),
    )


@cli.command("edit-topic")
@click.option("--instruction", required=True, help="What to change, in plain language.")
@click.option("--topic-name", required=True, help="Topic in the registry whose documents to edit.")
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(path_type=Path),
    help="Topic registry to resolve --topic-name against.",
)
@click.option(
    "--host",
    default=None,
    help="Confluence base URL, e.g. https://x.atlassian.net/wiki. Edits the live pages. "
    "Give this or --content-dir, not both.",
)
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Edit the topic's markdown files in this content tree instead of the live "
    "pages, so the change is reviewed as a pull request and published on merge. "
    "Give this or --host, not both.",
)
@click.option(
    "--tiers",
    default=None,
    help="Comma-separated subset of tiers to edit (e.g. 'standard,procedure'). "
    "Default: every page the topic declares.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Actually publish. Without this nothing in Confluence changes.",
)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt before publishing.")
@click.option(
    "--allow-macros",
    is_flag=True,
    help="Proceed even though a page uses Confluence macros this tool cannot round-trip.",
)
@click.option(
    "--allow-reader-directed",
    is_flag=True,
    help="Proceed even though a page contains text addressed to the reader of a "
    "prompt rather than to the organization. Look at the page first.",
)
@click.option(
    "--out-dir",
    default=Path("output/edits"),
    type=click.Path(path_type=Path),
    help="Where revised markdown and plans are written.",
)
@click.option(
    "--history-dir",
    default=_DEFAULT_HISTORY_DIR,
    type=click.Path(path_type=Path),
    help="Where local version history is recorded (default: output/.history).",
)
def edit_topic_cmd(
    instruction: str,
    topic_name: str,
    topics_path: Path,
    host: str | None,
    content_dir: Path | None,
    tiers: str | None,
    do_apply: bool,
    yes: bool,
    allow_macros: bool,
    allow_reader_directed: bool,
    out_dir: Path,
    history_dir: Path,
):
    """Apply one instruction across a topic's whole document set.

    A change like "access reviews move to monthly" lands differently in each
    tier: the Standard states the requirement, the Procedure carries the
    steps, and the Policy usually shouldn't change at all. Each page is
    planned separately and tier-aware, but reviewed and published as one
    change - and a page whose plan comes back empty is left alone rather than
    having an edit forced into it.
    """
    from policyforge.edit.session import EditTarget
    from policyforge.topics.registry import load_topics

    if bool(host) == bool(content_dir):
        raise click.UsageError(
            "Give exactly one of --host (edit the live Confluence pages) or "
            "--content-dir (edit the markdown files in a content tree, reviewed as a "
            "pull request)."
        )

    registry = load_topics(topics_path)
    topic = next((t for t in registry if t.name.lower() == topic_name.lower()), None)
    if topic is None:
        raise click.UsageError(
            f"No topic named {topic_name!r} in {topics_path}. Available: "
            + ", ".join(sorted(t.name for t in registry))
        )

    if content_dir is not None:
        wanted = {t.strip().lower() for t in tiers.split(",") if t.strip()} if tiers else None
        _edit_tree_run(
            topic=topic,
            content_dir=content_dir,
            tiers=wanted,
            instruction=instruction,
            do_apply=do_apply,
            yes=yes,
            allow_reader_directed=allow_reader_directed,
            out_dir=out_dir,
            config=load_config(),
        )
        return

    space = (topic.confluence or {}).get("space")
    pages = topic.confluence_pages()
    if not space or not pages:
        raise click.UsageError(
            f"Topic {topic.name!r} has no `confluence:` block naming its space and "
            "pages, so there is nothing to edit. Add one to the registry:\n"
            "    confluence:\n"
            "      space: ENG\n"
            "      pages:\n"
            '        standard: "<page title>"'
        )

    if tiers:
        wanted = {t.strip().lower() for t in tiers.split(",") if t.strip()}
        pages = [(tier, title) for tier, title in pages if tier.lower() in wanted]
        if not pages:
            raise click.UsageError(
                f"Topic {topic.name!r} declares no pages for tier(s) {tiers!r}. It has: "
                + ", ".join(tier for tier, _ in topic.confluence_pages())
            )

    click.echo(
        f"Topic {topic.name!r} (owner: {topic.owner or 'unassigned'}) — "
        f"{len(pages)} page(s) in {space}"
    )
    _edit_run(
        targets=[EditTarget(space=space, title=title, tier=tier) for tier, title in pages],
        instruction=instruction,
        host=host,
        do_apply=do_apply,
        yes=yes,
        allow_macros=allow_macros,
        allow_reader_directed=allow_reader_directed,
        out_dir=out_dir,
        history_dir=history_dir,
        config=load_config(),
    )


def _edit_tree_run(
    *,
    topic,
    content_dir: Path,
    tiers: set[str] | None,
    instruction: str,
    do_apply: bool,
    yes: bool,
    allow_reader_directed: bool,
    out_dir: Path,
    config: dict,
):
    """`edit-topic --content-dir`: the same review, written to files.

    Everything between reading and writing is `_edit_run`'s review, called
    rather than copied — the injection refusal, the fenced plan, the rewrite,
    the full diff and every `check_edit` warning. What differs is the edges.
    Documents come from the tree rather than the wiki, only their bodies are
    shown to the model, and on --apply the revision is written back into the
    files with the plan beside each one. No git command is run; the ones that
    would turn this into a pull request are printed.

    No local version history is recorded, unlike the wiki path. The tree is
    under version control, and a second history beside git is the duplication
    P-05 moved provenance out of.
    """
    import re

    from policyforge.edit import tree
    from policyforge.edit.session import EditTarget

    if not content_dir.exists():
        raise click.UsageError(f"No content tree at {content_dir}.")

    selection = tree.select_topic_files(content_dir, topic, tiers=tiers)
    if selection.ambiguous:
        detail = "\n".join(
            f"  {tier}: {', '.join(paths)}" for tier, paths in selection.ambiguous.items()
        )
        raise click.UsageError(
            f"More than one file in {content_dir} claims a tier of {topic.name!r}:\n"
            f"{detail}\nEditing one would leave the other saying the old thing. Remove "
            "the duplicate, or fix its `topic:` or `confluence.title` frontmatter."
        )
    if not selection.files:
        raise click.UsageError(
            f"No file in {content_dir} belongs to topic {topic.name!r}. A file belongs "
            "to a topic when its frontmatter says `topic: <name>`, or when its "
            "`confluence.title` matches a page the registry declares for that tier — "
            "which is what `policyforge pull` writes."
        )

    files_by_target: dict[int, tree.TreeFile] = {}
    targets = []
    for tier, tree_file in selection.files.items():
        _frontmatter, body = tree.split_frontmatter(tree_file.path.read_text(encoding="utf-8"))
        target = EditTarget(
            space="", title=tree_file.title or tree_file.relative, tier=tier, original=body
        )
        files_by_target[id(target)] = tree_file
        targets.append(target)
        click.echo(f"Read {tree_file.relative} ({tier}; matched by {tree_file.matched_by})")

    click.echo(
        f"Topic {topic.name!r} (owner: {topic.owner or 'unassigned'}) — "
        f"{len(targets)} file(s) in {content_dir}"
    )

    _refuse_reader_directed(targets, allow_reader_directed)

    provider = get_provider(config)
    slugs = {id(t): re.sub(r"[^a-z0-9]+", "-", t.title.lower()).strip("-") for t in targets}
    publishable = _review_edits(
        targets, instruction, provider, out_dir=out_dir, slugs=slugs, words=_TREE
    )
    if not publishable:
        return

    if do_apply:
        paths = [files_by_target[id(o.target)].path for o in publishable]
        dirty = tree.uncommitted(paths, cwd=content_dir)
        if dirty is None:
            click.echo(
                "WARNING: could not ask git whether these files have uncommitted "
                "changes. If they do, the model's edit and yours will land in one "
                "diff and a reviewer cannot tell them apart."
            )
        elif dirty:
            raise click.UsageError(
                "These files have uncommitted changes:\n"
                + "\n".join(f"  {path}" for path in dirty)
                + "\nCommit or stash them first. Written on top, the model's edit and "
                "the uncommitted change would be one diff, and the person reviewing "
                "the pull request could not tell which change the plan made. A file "
                "you have just pulled counts — commit the pull, then edit."
            )

    if not _confirm_edits(
        publishable,
        do_apply=do_apply,
        yes=yes,
        dry_run=(
            f"\nDry run — no file in {content_dir} changed. Re-run with --apply to "
            f"write {len(publishable)} file(s)."
        ),
        prompt="Write edits to",
    ):
        return

    import json

    written, failed = [], []
    for outcome in publishable:
        tree_file = files_by_target[id(outcome.target)]
        try:
            tree.write_revision(tree_file, outcome.revised)
        except tree.TreeEditError as exc:
            failed.append((tree_file, str(exc)))
            continue
        # The plan goes beside the document and into the pull request with
        # it: what was asked, what the model intended, and what it declined.
        plan_path = tree_file.path.with_suffix(".plan.json")
        write_text_lf(plan_path, json.dumps(outcome.plan.as_record(), indent=2) + "\n")
        written.append((tree_file, plan_path))
        click.echo(f"Wrote {tree_file.relative} (plan: {plan_path.name})")

    if written:
        click.echo("")
        click.echo(
            tree.git_instructions(
                branch=tree.suggest_branch(topic.name, instruction),
                files=[tree.display_path(f.path, cwd=content_dir) for f, _ in written],
                plans=[tree.display_path(p, cwd=content_dir) for _, p in written],
                topic_name=topic.name,
                instruction=instruction,
            )
        )

    if failed:
        click.echo("")
        for tree_file, message in failed:
            click.echo(f"FAILED {tree_file.relative}: {message}")
        raise SystemExit(1)


@cli.command("import-confluence")
@click.option(
    "--tier",
    type=click.Choice(["standard", "policy", "procedure"]),
    required=True,
    help="Which document tier this Confluence page corresponds to - determines which "
    "local version-history stream the import is recorded into.",
)
@click.option(
    "--name",
    required=True,
    help="Document slug/filename stem, matching what `policyforge generate` used, "
    "e.g. 'authenticator-mgmt'. Determines the version-history stream and the "
    "default --out path.",
)
@click.option("--space", required=True, help="Confluence space key to read from.")
@click.option("--title", required=True, help="Page title to look up.")
@click.option(
    "--host",
    required=True,
    help="Confluence base URL, e.g. https://yourorg.atlassian.net/wiki.",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to write the imported markdown "
    "(default: output/<tier>s/<name>.imported.md - deliberately not the same "
    "filename `generate` writes, so an import never silently overwrites a fresh draft).",
)
@click.option(
    "--history-dir",
    default=_DEFAULT_HISTORY_DIR,
    type=click.Path(path_type=Path),
    help="Where local version history is recorded (default: output/.history).",
)
def import_confluence_cmd(
    tier: str, name: str, space: str, title: str, host: str, out: Path | None, history_dir: Path
):
    """Pull a page's current content back out of Confluence, converting it to
    markdown, and record it into the same local version-history stream
    `generate` uses for --tier/--name - so you can diff what this tool last
    generated against what's actually live (e.g. after a manual edit)."""
    from datetime import datetime, timezone

    from policyforge.content.provenance import (
        PREVIOUSLY_GENERATED_BY,
        carried_content_class,
        declared_content_class,
        import_metadata,
        read_generated_by,
    )
    from policyforge.content.tree import (
        LEGACY_TIER_DIRS,
        TIER_DIRECTORY,
        ContentError,
        parse_document,
        render_document,
    )
    from policyforge.export.confluence_importer import (
        confluence_to_markdown,
        fetch_confluence_page,
    )
    from policyforge.history.version_store import load_history, record_version

    # Checked before the network call, not after: a name that cannot be
    # stored is not worth fetching a page for.
    name = _checked_slug(name)

    page = fetch_confluence_page(space=space, title=title, host=host)
    markdown_text = confluence_to_markdown(page.storage_body)

    directory = TIER_DIRECTORY[tier]  # the one table, as `generate` reads it (#411)
    out_path = out or Path("output") / directory / f"{name}.imported.md"
    slug = f"{tier}/{name}"
    previous = load_history(history_dir, slug)

    # What the local predecessors said about this document, read BEFORE
    # anything is written, because --out may be one of them (#197). The
    # generated file, and whatever sits at --out already (usually the last
    # import), then every recorded version.
    classes: list[str] = []
    earlier_generation: dict = {}
    # The generated file is read where `generate` writes it now, and where it
    # wrote it before #411 (`policys/`), so a tree generated earlier still
    # hands its content class to the import. Missing it would drop a
    # licensed class silently, the direction #197 exists to prevent.
    legacy = [old for old, new in LEGACY_TIER_DIRS.items() if new == directory]
    generated = [Path("output") / d / f"{name}.md" for d in (directory, *legacy)]
    for candidate in (*generated, out_path):
        try:
            metadata = parse_document(
                candidate.read_text(encoding="utf-8"), path=candidate, root=candidate.parent
            ).metadata
        except (OSError, UnicodeDecodeError, ContentError):
            continue
        classes.append(declared_content_class(metadata))
        earlier_generation = earlier_generation or (
            read_generated_by(metadata) or dict(metadata.get(PREVIOUSLY_GENERATED_BY) or {})
        )
    classes += [str(v.metadata.get("content_class") or "") for v in previous]
    content_class = carried_content_class(classes)

    imported_from = {
        "space": space,
        "title": title,
        "page_version": page.version,
        "date": datetime.now(timezone.utc).date().isoformat(),
    }
    # No `generated_by` block, ever: the page may have been edited by a
    # person. The earlier generation is kept whole under
    # `previously_generated_by`, which attribution does not read, so it is
    # history rather than a claim about this text.
    metadata = import_metadata(
        imported_from=imported_from,
        content_class=content_class,
        previously_generated_by=earlier_generation,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out_path, render_document(metadata, markdown_text))
    click.echo(f"Imported {title!r} from Confluence -> {out_path}")
    if content_class:
        click.echo(f"Content class {content_class!r}, carried from the local predecessor.")
    else:
        click.echo(
            "Content class unknown: this document is governed by the wiki's visibility "
            "rule until one is set. No local predecessor says what it was drawn from, "
            "and the import does not guess."
        )

    record = record_version(
        history_dir,
        slug,
        markdown_text,
        source="confluence-import",
        metadata={
            "space": space,
            "title": title,
            "page_version": page.version,
            **({"content_class": content_class} if content_class else {}),
        },
    )

    if record is None:
        latest = previous[-1].version if previous else None
        click.echo(
            f"Matches the last recorded version of {slug!r} (v{latest}) — no drift detected."
        )
    elif previous:
        click.echo(
            f"Differs from the last recorded version (v{previous[-1].version}) — recorded as "
            f"{slug!r} v{record.version}. "
            + history_hint(
                tier=tier,
                name=name,
                previous=previous[-1].version,
                current=record.version,
            )
        )
    else:
        click.echo(f"Recorded as {slug!r} v{record.version} (first version in this stream).")


@cli.command("check")
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Markdown content tree to check. Defaults to `zardoz.content_dir`, else docs/.",
)
@click.option(
    "--synthesis-dir",
    default=Path("output/synthesis"),
    type=click.Path(path_type=Path),
    help="Where the synthesis files live, for the dropped-citation check. "
    "Skipped if the directory isn't there.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Treat warnings as failures too. For a repo that has finished migrating.",
)
@click.option(
    "--entail",
    is_flag=True,
    help="Also ask a model whether each cited obligation is actually carried by "
    "the synthesis requirements it cites. Costs one call per cited obligation, "
    "printed before anything runs. Reported only -- never changes the exit code.",
)
def check_cmd(content_dir: Path | None, synthesis_dir: Path, strict: bool, entail: bool):
    """Check the content tree before anything is published.

    Entirely offline, so it runs on a pull request from a fork with no
    credentials - which is where you want it. Catches the mistakes that
    survive review and fail later: two files claiming one Confluence page,
    a link to a document somebody renamed, a rewrite that dropped the
    framework citations a document exists to carry.
    """
    from policyforge.content.check import check_tree

    root = _content_dir(content_dir)
    if not root.exists():
        raise click.UsageError(
            f"No content directory at {root}. Pass --content-dir, or set "
            "`zardoz.content_dir` in config/config.yaml."
        )

    from policyforge.org.context import org_actors

    # The organization's own names, so the Playbook check catches "..., and
    # <Org> will adopt it" for THIS organization (#323). The config also
    # tells the licensed-catalog scan where this project holds licensed
    # content (9b on #10).
    config = load_config_or_empty()
    report = check_tree(
        root, synthesis_dir=synthesis_dir, org_actors=org_actors(config), config=config
    )
    click.echo(report.format_report())

    # Licensed catalog content committed to a repository that has not
    # declared the right to hold it is a licence breach, not a lint. It
    # belongs in the same gate as everything else that must not reach a
    # merge, and it needs no credentials to check.
    from policyforge.frameworks.registry import check_licences

    licences = check_licences(config)
    if licences.findings:
        click.echo("")
        click.echo("Framework licences:")
        for finding in licences.findings:
            mark = "ERROR" if finding.severity == "error" else "warn "
            click.echo(f"  {mark}  {finding.framework.id}: {finding.message}")

    if entail:
        _report_entailment(root, synthesis_dir)

    # Deliberately after the entailment report and deliberately ignoring it.
    # A model's verdict can differ between runs on identical input; a
    # malformed document cannot. Putting the first behind this number would
    # change what a non-zero exit means for every caller already relying on
    # it -- including `--strict`, which promotes warnings to a failure and so
    # would promote an opinion too.
    if not report.ok or not licences.ok or (strict and report.warnings):
        raise SystemExit(1)


def _report_entailment(root: Path, synthesis_dir: Path) -> None:
    """Judge cited obligations against their premises, and say what it costs first.

    **The count is exact rather than estimated**: it is the number of cited
    obligations that have a premise to judge, computed without calling
    anything. A price per call would be a guess -- it depends on the model
    and on how long each requirement is -- and a number nobody can stand
    behind is worse than the count itself.
    """
    from policyforge.content.grounding import judgeable, ungrounded
    from policyforge.content.tree import load_content_tree
    from policyforge.entail import get_entailer

    config = load_config_or_empty()
    entailer = get_entailer(config)
    if entailer is None:
        click.echo("")
        click.echo("Entailment: no judge configured — set `entail.provider` and `entail.model`.")
        return
    if not synthesis_dir.exists():
        click.echo("")
        click.echo(f"Entailment: no synthesis at {synthesis_dir}, so nothing to judge against.")
        return

    documents, _ = load_content_tree(root)
    work = []
    for doc in documents:
        source = synthesis_dir / f"{doc.slug}.md"
        if not source.exists():
            continue
        synthesis = source.read_text(encoding="utf-8")
        work.append((doc, synthesis, len(judgeable(doc.body, synthesis))))

    total = sum(count for _, _, count in work)
    click.echo("")
    click.echo(f"Entailment: {total} cited obligation(s) to judge, one model call each.")
    if not total:
        return

    findings = 0
    for doc, synthesis, count in work:
        if not count:
            continue
        for finding in ungrounded(doc.body, synthesis, entailer):
            findings += 1
            click.echo(f"  {doc.relative_path}: {finding}")
    if not findings:
        click.echo("  Every cited obligation is carried by what it cites.")
    click.echo("  These are opinions, and do not affect the exit code.")


#: The two stores `publish`, `wiki-drift` and `pull` can talk to. Spelled
#: with a hyphen on the command line and with an underscore in frontmatter,
#: because one is a flag and the other is a YAML key; `github_wiki.py` holds
#: both spellings so they cannot drift apart.
TARGETS = ("confluence", "github-wiki")


def _target_kind(target: str | None, config: dict) -> str:
    """Which store this run publishes to: the flag, else config, else Confluence."""
    return target or str((config.get("publish") or {}).get("target") or "confluence")


def _wiki_publisher(config: dict, *, repository: str, allow_public: bool, token_env: str = ""):
    """The GitHub-wiki adapter, with the wiki's visibility already asked.

    Visibility is resolved here rather than inside the adapter so that one
    question is asked once per run, and so the answer — including "could not
    tell" — is on the plan's first line whatever happens afterwards.
    """
    from policyforge.export._wiki_auth import repository_visibility
    from policyforge.export.github_wiki import GitHubWikiPublisher

    block = (config.get("publish") or {}).get("github_wiki") or {}
    name = repository or str(block.get("repository") or "")
    if not name or name.count("/") != 1:
        raise click.UsageError(
            "No GitHub wiki repository. Pass --repository owner/name, or add one to "
            "config/config.yaml:\n"
            "    publish:\n"
            "      target: github-wiki\n"
            "      github_wiki:\n"
            "        repository: acme/security-policies"
        )

    # The flag wins over config, because a CI job has no config file and
    # names its secret on the command line — the variable *name*, never the
    # token, which stays in the environment.
    token_env = token_env or str(block.get("token_env") or "")
    owner, repo = name.split("/")
    return GitHubWikiPublisher(
        name,
        # Under output/, which is gitignored and outside the Docker build
        # context, so a wiki clone never lands in a tracked tree or an image.
        workdir=Path("output") / ".wiki" / f"{owner}-{repo}",
        token_env=token_env,
        public=repository_visibility(name, token_env),
        allow_public=allow_public,
    )


def _publisher_for(
    kind: str,
    config: dict,
    *,
    host: str = "",
    repository: str = "",
    allow_public: bool = False,
    token_env: str = "",
):
    """The adapter for this run, or a usage error naming what is missing."""
    if kind == "github-wiki":
        return _wiki_publisher(
            config, repository=repository, allow_public=allow_public, token_env=token_env
        )

    from policyforge.export.publisher import ConfluencePublisher

    if not host:
        raise click.UsageError(
            "No Confluence host. Pass --host, or add one to config/config.yaml:\n"
            "    zardoz:\n"
            "      host: https://yourorg.atlassian.net/wiki"
        )
    return ConfluencePublisher(host=host)


def _refuse_public_wiki(publisher) -> None:
    """A public wiki needs saying yes to, and unknown counts as public."""
    from policyforge.export.github_wiki import GitHubWikiPublisher

    if not isinstance(publisher, GitHubWikiPublisher):
        return
    if publisher.public is False or publisher.allow_public:
        return

    click.echo(publisher.plan_header())
    raise click.UsageError(
        "This wiki is public, or its visibility could not be determined, and "
        "publishing would put your documents where anyone can read them. Pass "
        "--allow-public once you have decided that is what you want. Documents "
        "carrying licensed catalog content are never published to a public wiki, "
        "with or without that flag."
    )


@cli.command("publish")
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Markdown content tree to publish from.",
)
@click.option("--host", default="", help="Confluence base URL. Defaults to `zardoz.host`.")
@click.option(
    "--target",
    default=None,
    type=click.Choice(TARGETS),
    help="Which store to publish to. Defaults to `publish.target`, else confluence.",
)
@click.option(
    "--repository",
    default="",
    help="GitHub wiki repository as owner/name. Defaults to `publish.github_wiki.repository`.",
)
@click.option(
    "--token-env",
    default="",
    help="Name of the environment variable holding the GitHub token. Defaults to "
    "`publish.github_wiki.token_env`; without either, git uses your credential helper.",
)
@click.option(
    "--allow-public",
    is_flag=True,
    help="Publish to a wiki that is public, or whose visibility could not be "
    "determined. Licensed catalog content is never published to a public wiki "
    "whether or not this is passed.",
)
@click.option(
    "--only",
    default="",
    help="Only publish documents whose path contains this substring, so a CI job "
    "can republish just what a merge touched.",
)
@click.option("--apply", "apply_", is_flag=True, help="Actually publish. Without it, plan only.")
@click.option(
    "--allow-macros",
    is_flag=True,
    help="Publish over pages using macros this tool cannot round-trip. This "
    "flattens them; do not pass it to get past a skip you haven't read.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite pages changed on the wiki since this tool last wrote them. That "
    "destroys the change; pull it and review instead unless you have decided otherwise.",
)
def publish_cmd(
    content_dir: Path | None,
    host: str,
    target: str | None,
    repository: str,
    token_env: str,
    allow_public: bool,
    only: str,
    apply_: bool,
    allow_macros: bool,
    force: bool,
):
    """Publish the content tree to the pages its frontmatter declares.

    Each document names its own destination, so the file-to-page mapping
    lives in the repository under review rather than in a workflow argument.
    A document with no `confluence:` block is not published, which is how a
    draft stays a draft.

    A page somebody edited on the wiki since this tool last wrote it is not
    overwritten: it is reported as moved and the run exits non-zero, so a CI
    job fails where it would otherwise have destroyed the edit in silence.

    Plans by default and writes nothing; pass --apply once you have read it.
    """
    from policyforge.content.tree import load_content_tree
    from policyforge.export.publisher import publish_documents

    root = _content_dir(content_dir)
    if not root.exists():
        raise click.UsageError(f"No content directory at {root}.")

    config = load_config_or_empty()
    kind = _target_kind(target, config)
    publisher = _publisher_for(
        kind,
        config,
        host=_zardoz_setting(config, "host", host) if kind == "confluence" else "",
        repository=repository,
        allow_public=allow_public,
        token_env=token_env,
    )
    _refuse_public_wiki(publisher)

    documents, problems = load_content_tree(root)
    report = publish_documents(
        publisher,
        documents,
        problems,
        dry_run=not apply_,
        allow_unsupported=allow_macros,
        only=only,
        force=force,
    )
    click.echo(report.format_report())
    # A moved page fails the run even when others published. Exiting 0 would
    # let a CI job go green while an edit sat on the wiki unpulled — the
    # silent case this check exists to end.
    if report.moved or (report.skipped and not report.published):
        raise SystemExit(1)


@cli.command("wiki-drift")
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Markdown content tree to compare against.",
)
@click.option("--host", default="", help="Confluence base URL. Defaults to `zardoz.host`.")
@click.option(
    "--target",
    default=None,
    type=click.Choice(TARGETS),
    help="Which store to compare against. Defaults to `publish.target`, else confluence.",
)
@click.option(
    "--repository",
    default="",
    help="GitHub wiki repository as owner/name. Defaults to `publish.github_wiki.repository`.",
)
@click.option(
    "--token-env",
    default="",
    help="Name of the environment variable holding the GitHub token. Defaults to "
    "`publish.github_wiki.token_env`; without either, git uses your credential helper.",
)
@click.option(
    "--only",
    default="",
    help="Only look at documents whose path contains this substring.",
)
@click.option(
    "--fail-on-change",
    is_flag=True,
    help="Exit non-zero when any page has moved, so a scheduled run is the notification.",
)
def wiki_drift_cmd(
    content_dir: Path | None,
    host: str,
    target: str | None,
    repository: str,
    token_env: str,
    only: str,
    fail_on_change: bool,
):
    """Which published pages changed on the wiki since this tool wrote them.

    The question a policy owner asks before a review cycle. `publish` answers
    it too, but only as the reason it refused to write, which is the wrong
    moment to find out. This writes nothing and prints the `pull` command
    that would bring each edit into the repository as a reviewable diff.

    A page whose body already says what the repository says is in sync,
    whoever wrote its latest version.
    """
    from policyforge.content.tree import load_content_tree
    from policyforge.export.publisher import drift_documents

    root = _content_dir(content_dir)
    if not root.exists():
        raise click.UsageError(f"No content directory at {root}.")

    config = load_config_or_empty()
    kind = _target_kind(target, config)
    publisher = _publisher_for(
        kind,
        config,
        host=_zardoz_setting(config, "host", host) if kind == "confluence" else "",
        repository=repository,
        token_env=token_env,
    )

    documents, _ = load_content_tree(root)
    report = drift_documents(publisher, documents, only=only)
    click.echo(report.format_report())
    if fail_on_change and report.moved:
        raise SystemExit(1)


@cli.command("pull")
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Markdown content tree to write into.",
)
@click.option("--host", default="", help="Confluence base URL. Defaults to `zardoz.host`.")
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(path_type=Path),
    help="Registry whose declared pages to pull. Ignored if --title is given.",
)
@click.option(
    "--target",
    default=None,
    type=click.Choice(TARGETS),
    help="Which store to pull from. Defaults to `publish.target`, else confluence.",
)
@click.option(
    "--repository",
    default="",
    help="GitHub wiki repository as owner/name. Defaults to `publish.github_wiki.repository`.",
)
@click.option(
    "--token-env",
    default="",
    help="Name of the environment variable holding the GitHub token. Defaults to "
    "`publish.github_wiki.token_env`; without either, git uses your credential helper.",
)
@click.option("--space", default="", help="Pull one page: its space key (Confluence only).")
@click.option("--title", default="", help="Pull one page: its exact title.")
@click.option(
    "--tier",
    default="standard",
    type=click.Choice(["policy", "standard", "procedure"]),
    show_default=True,
    help="Tier for a single --title pull, which decides where it lands.",
)
@click.option(
    "--apply", "apply_", is_flag=True, help="Actually write files. Without it, plan only."
)
@click.option(
    "--allow-macros",
    is_flag=True,
    help="Pull pages whose macros would not survive a later publish. The file "
    "will look correct and destroy them the first time it is published.",
)
def pull_cmd(
    content_dir: Path | None,
    host: str,
    target: str | None,
    repository: str,
    token_env: str,
    topics_path: Path,
    space: str,
    title: str,
    tier: str,
    apply_: bool,
    allow_macros: bool,
):
    """Bring live Confluence pages down into the tree as markdown.

    The way back. Somebody will edit the wiki directly - that is what a wiki
    is for - and without this, every such edit is either lost on the next
    publish or quietly makes the repo wrong. Pulling turns it into a diff on
    a branch.

    Pages using macros this tool cannot round-trip are refused rather than
    written, because the file would look correct and destroy them on the
    first publish.
    """
    from policyforge.export.publisher import pull_documents
    from policyforge.export.pull import pages_from_topics, target_path
    from policyforge.topics.registry import load_topics

    root = _content_dir(content_dir)

    config = load_config_or_empty()
    kind = _target_kind(target, config)
    publisher = _publisher_for(
        kind,
        config,
        host=_zardoz_setting(config, "host", host) if kind == "confluence" else "",
        repository=repository,
        token_env=token_env,
    )

    if title:
        # A wiki page needs no space: the repository is the container, and it
        # comes from config or --repository. That is why the reconcile
        # command a wiki prints names only a title.
        if kind == "confluence" and not space:
            raise click.UsageError("--title needs --space to say which space to look in.")
        targets = [(space or publisher.repository, title, tier)]
    else:
        try:
            targets = pages_from_topics(load_topics(topics_path), kind=kind)
        except FileNotFoundError:
            raise click.UsageError(
                f"No topic registry at {topics_path}, and no --title given, so there is "
                "nothing to pull. Name a page with --title, or declare your "
                "pages in the registry."
            ) from None
        if not targets:
            raise click.UsageError(
                f"No topic declares a `{kind}:` block, so there are no pages to pull."
            )

    click.echo(f"Pulling {len(targets)} page(s) into {root}")
    report = pull_documents(
        publisher,
        targets,
        root=root,
        target_path=target_path,
        dry_run=not apply_,
        allow_unsupported=allow_macros,
    )
    click.echo("")
    click.echo(report.format_report())
    if report.refused:
        raise SystemExit(1)

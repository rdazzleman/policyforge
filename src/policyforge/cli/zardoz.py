"""The Zardoz shell, topic discovery, corpus sync, and the MCP server."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import (
    _DEFAULT_HISTORY_DIR,
    _zardoz_setting,
    get_provider,
    load_config,
    load_config_or_empty,
)


@cli.command("mcp")
@click.option(
    "--corpus-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Synced document snapshot (default: the same one `zardoz` uses).",
)
@click.option(
    "--topics",
    "topics_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Topic registry (default: config/topics.yaml).",
)
def mcp_cmd(corpus_dir: Path | None, topics_path: Path | None):
    """Serve the read-only analyses as MCP tools over stdio.

    Lets Claude Code, Claude Desktop or any MCP client ask this repository
    which controls nobody owns, what a team is accountable for, or where a
    HIPAA citation is addressed - with the content boundary, the ledger and
    the citation checks all still in the path, because the answers come from
    the same functions the CLI calls.

    Read-only, structurally: a test walks this package's AST and fails if any
    module so much as names the publish path. Editing a live policy page
    stays here in the CLI, behind its dry run, macro check, version guard and
    explicit confirmation.

    This speaks stdio and is spawned by a client; it binds no port. Configure
    it as a command, not a URL.
    """
    from policyforge.mcp.server import serve

    config = load_config_or_empty()

    try:
        serve(config, corpus_dir=corpus_dir, topics_path=topics_path)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.group("zardoz", invoke_without_command=True)
@click.option(
    "--topics",
    "topics_path",
    default=Path("config/topics.yaml"),
    type=click.Path(path_type=Path),
    help="Topic registry to load, for ownership and page lookups.",
)
@click.option(
    "--corpus-dir",
    default=Path("output/.zardoz"),
    type=click.Path(path_type=Path),
    help="Where the synced document snapshot lives.",
)
@click.option("--no-art", is_flag=True, help="Skip the floating head on launch.")
@click.option(
    "--plain",
    is_flag=True,
    help="Drop the Zardoz voice from the shell chrome entirely. Answers are "
    "plain either way - this only affects greetings, prompts and errors.",
)
@click.pass_context
def zardoz_cmd(ctx, topics_path: Path, corpus_dir: Path, no_art: bool, plain: bool):
    """Ask questions about your published policy set, conversationally.

    Run with no subcommand to open the shell. Zardoz reads; it does not
    write. It can draft a `policyforge edit-topic` command for you to run,
    but every change to a live page still goes through that command's gates.
    """
    from policyforge.zardoz.startup import load_registry, open_session

    # Loaded before deciding whether to open a shell, because `sync` and
    # `discover` need the registry too.
    registry = load_registry(topics_path)
    topics, _note = registry

    ctx.ensure_object(dict)
    ctx.obj.update(topics=topics, topics_path=topics_path, corpus_dir=corpus_dir)
    if ctx.invoked_subcommand is not None:
        return

    from policyforge.zardoz.art import banner
    from policyforge.zardoz.shell import run_shell

    # Loading and explaining what could not be loaded is shared with the MCP
    # server — see zardoz/startup.py for why the two must not decide
    # separately what is worth mentioning.
    session = open_session(
        topics_path=topics_path,
        corpus_dir=corpus_dir,
        config=load_config_or_empty(),
        provider_factory=get_provider,
        registry=registry,
        plain=plain,
        history_dir=_DEFAULT_HISTORY_DIR,
    )

    click.echo(banner(art=not no_art, plain=plain))
    for note in session.notes:
        click.echo(note)
    click.echo("")

    run_shell(
        session.state,
        read=lambda prompt: click.prompt(prompt, prompt_suffix="", show_default=False),
        write=click.echo,
    )


@zardoz_cmd.command("discover")
@click.option("--space", required=True, help="Confluence space key to crawl.")
@click.option("--host", default="", help="Confluence base URL. Defaults to `zardoz.host`.")
@click.option(
    "--out",
    default=Path("config/topics.proposed.yaml"),
    type=click.Path(path_type=Path),
    help="Where to write the proposal. Deliberately not topics.yaml - it needs "
    "owners set before it is usable.",
)
@click.option(
    "--max-results",
    default=500,
    show_default=True,
    help="Refuse to crawl a space larger than this rather than proposing a "
    "registry from an arbitrary subset of it.",
)
@click.option(
    "--no-llm",
    is_flag=True,
    help="Group only by naming convention and control citations. Pages no "
    "convention reaches are listed rather than clustered.",
)
@click.pass_context
def zardoz_discover(ctx, space: str, host: str, out: Path, max_results: int, no_llm: bool):
    """Propose a topic registry from a space nobody has catalogued.

    Most of the grouping is already written down, just not as data: a
    governance space names its pages by convention (Access Control Policy,
    Access Control Standard) and cites the same controls throughout a related
    set. Those signals are exact and free, and they place the majority of a
    real space with no model involved. The LLM is used only for the residue.

    Every proposed owner is [UNASSIGNED]. Nothing in a page reliably says
    which team is accountable for it, and a wrong owner in a compliance
    artifact is worse than a blank one.
    """
    from policyforge.export.confluence_search import search_pages, space_cql
    from policyforge.zardoz.discover import discover_topics, render_registry

    config = load_config_or_empty()
    host = _zardoz_setting(config, "host", host)
    if not host:
        raise click.UsageError("No Confluence host. Pass --host, or set `zardoz.host`.")

    provider = None
    if not no_llm:
        try:
            provider = get_provider(load_config())
        except (FileNotFoundError, KeyError, ValueError) as exc:
            click.echo(f"  (no usable llm config: {exc} — grouping by convention only)")

    click.echo(f"Crawling space {space}")
    pages = search_pages(host=host, cql=space_cql(space), with_body=True, max_results=max_results)
    report = discover_topics(pages, space=space, provider=provider)

    click.echo("")
    click.echo(report.format_report())

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_registry(report), encoding="utf-8")
    click.echo("")
    click.echo(f"Wrote {out}. Set the owners, check the groupings, then rename it to")
    click.echo("config/topics.yaml and run `policyforge coverage` against it.")


@zardoz_cmd.command("sync")
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Markdown content tree to read. Defaults to `zardoz.content_dir` in "
    "config.yaml. Needs no credentials.",
)
@click.option(
    "--host",
    default="",
    help="Confluence base URL. Defaults to `zardoz.host` in config.yaml. Omit "
    "to sync from markdown only.",
)
@click.option(
    "--supporting-space",
    default="",
    help="Extra space to pull as unowned supporting context. Defaults to "
    "`zardoz.supporting_space` in config.yaml. Omit for registry pages only.",
)
@click.option(
    "--max-results",
    default=500,
    show_default=True,
    help="Refuse to walk a supporting space larger than this, rather than "
    "silently syncing an arbitrary subset of it.",
)
@click.option(
    "--allow-empty",
    is_flag=True,
    help="Let a sync that resolves nothing clear the existing corpus. Without "
    "this, a run that finds no documents leaves the previous snapshot alone.",
)
@click.pass_context
def zardoz_sync(
    ctx,
    content_dir: Path | None,
    host: str,
    supporting_space: str,
    max_results: int,
    allow_empty: bool,
):
    """Build the local snapshot Zardoz answers from.

    Reads a markdown content tree, a Confluence space, or both. Markdown
    needs no credentials at all, so a repo-backed document set can be synced
    and questioned offline. Where both are configured the tree wins: in that
    arrangement the file is the source of truth and the page is a copy of it.

    Documents that know their owner - from the topic registry, or from their
    own frontmatter - are trusted; everything else is supporting context that
    answers may use and will say they used.
    """
    from policyforge.zardoz.corpus import sync_corpus

    topics = ctx.obj["topics"]
    corpus_dir = ctx.obj["corpus_dir"]

    config = load_config_or_empty()

    host = _zardoz_setting(config, "host", host)
    supporting_space = _zardoz_setting(config, "supporting_space", supporting_space)
    if content_dir is None:
        configured = _zardoz_setting(config, "content_dir", "")
        content_dir = Path(configured) if configured else None

    if content_dir is None and not host:
        raise click.UsageError(
            "Nothing to sync from. Point at a markdown tree, a Confluence host, or "
            "both:\n"
            "    policyforge zardoz sync --content-dir docs\n"
            "or in config/config.yaml:\n"
            "    zardoz:\n"
            "      content_dir: docs\n"
            "      host: https://yourorg.atlassian.net/wiki"
        )
    if content_dir is not None and not content_dir.exists():
        raise click.UsageError(
            f"No content directory at {content_dir}. Create it, point --content-dir "
            "somewhere else, or drop it and sync from Confluence only."
        )
    if host and not topics:
        raise click.UsageError(
            f"No topics loaded from {ctx.obj['topics_path']}, so there are no Confluence "
            "pages to sync. Copy config/topics.example.yaml and declare each topic's "
            "pages under a `confluence:` block, or sync from markdown only."
        )

    what = []
    if content_dir is not None:
        what.append(f"markdown under {content_dir}")
    if host:
        declared = sum(len(topic.confluence_pages()) for topic in topics)
        what.append(f"{declared} declared page(s) from {len(topics)} topic(s)")
        if supporting_space:
            what.append(f"space {supporting_space}")
    click.echo("Syncing " + ", plus ".join(what))

    report = sync_corpus(
        topics,
        host=host,
        content_dir=content_dir,
        supporting_space=supporting_space,
        corpus_dir=corpus_dir,
        max_results=max_results,
        allow_empty=allow_empty,
    )
    click.echo("")
    click.echo(report.format_report())
    if report.refused_empty:
        raise SystemExit(1)

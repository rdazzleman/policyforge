"""`policyforge library`: a traceable library of a licensed catalog's versions
(443, part 1). The logic is `policyforge.library`; this is the command."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli


@cli.group("library")
def library_group():
    """Keep your own licensed catalog's versions, each traceable to its export,
    with a change log between them (a private git repository of their own)."""


@library_group.command("add")
@click.option(
    "--library",
    "library_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The library: a git repository of its own whose framework.yaml names the parser "
    "and declares `allow_licensed_in_repo: true`.",
)
@click.option(
    "--export",
    "export_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The export as you received it (e.g. your MyCSF export). Copied byte for byte.",
)
@click.option("--version", "version", required=True, help="The version it is, e.g. 11.7.")
@click.option(
    "--content-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Your documents, so the change log names which cite each changed item. "
    "Default: the project's content tree, if there is one.",
)
def library_add_cmd(library_dir: Path, export_path: Path, version: str, content_dir: Path | None):
    """Add a version: copy the export, parse it with the framework's licensed
    parser, record where both came from, write the change log against the
    current version, then commit and tag `v<version>` in the library.

    Refused before anything is written: a library that isn't a repository of
    its own, sits inside a PolicyForge checkout, or hasn't declared it may hold
    licensed content; a version that exists; an export that is already a
    version under another name; a library with uncommitted changes or no git
    identity.
    """
    import datetime as dt

    import policyforge
    from policyforge import library as lib
    from policyforge.cli._common import _content_dir
    from policyforge.cli.etl import plugin_parse

    try:
        library = lib.open_library(library_dir)
        lib.check_add(library, export_path, version)
    except lib.LibraryError as exc:
        raise click.ClickException(f"{exc}\nNothing was written.") from exc

    def parse(export: Path, label: str):
        options = {**library.parser_options, library.export_option: export, "version": label}
        parser, result = plugin_parse(library.parser, sample=export, **options)
        click.echo(result.report)
        return result.controls, {
            "command": library.parser,
            "framework": parser.framework,
            "plugin": parser.origin,
            "source": result.source,
        }

    root = content_dir if content_dir is not None else _content_dir(None)
    content_root = root if root.is_dir() else None
    try:
        added = lib.add_version(
            library,
            export_path,
            version,
            parse=parse,
            content_root=content_root,
            now=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            tool={"version": policyforge.__version__},
        )
    except lib.LibraryError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"\nAdded version {added.version} -> {added.folder}")
    if added.change_log is not None:
        click.echo(f"Change log from {added.previous}: {added.change_log}")
    else:
        click.echo("The library's first version: no change log yet.")
    click.echo(f"Committed {added.commit[:12]} and tagged {added.tag}. CURRENT is {added.version}.")
    if added.attributes is not None:
        click.echo(
            f"{'Wrote' if added.attributes == 'written' else 'Added to'} the library's "
            f"{lib.ATTRIBUTES}: `{lib.NO_CONVERSION}`, so git stores and checks out every file "
            "as received, with no line-ending conversion."
        )
        if added.previous is not None:
            # An existing library (461, 80's ruling): what is already
            # committed or checked out may have been converted.
            click.echo(
                "  Versions added before this commit may have been stored with converted line "
                "endings, and checkouts made before it may hold converted bytes: re-clone the "
                "library, and check each earlier version's export against its provenance.json."
            )

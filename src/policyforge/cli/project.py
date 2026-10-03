"""Starting a project directory, for an install that is not a clone."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli


@cli.command("init")
@click.argument(
    "directory",
    required=False,
    default=Path("."),
    type=click.Path(file_okay=False, path_type=Path),
)
def init_cmd(directory: Path):
    """Lay out a PolicyForge project: bundled catalogs, example configs, .gitignore.

    Every command reads `config/` and `data/frameworks/` relative to where it
    runs. A clone of the repository already has them; an installed package
    (Homebrew, pipx, pip) does not, and this writes them. The public-domain
    catalogs are copied in - NIST 800-53, FedRAMP, ARC-AMPE, the HIPAA
    Security Rule - with a README for each bring-your-own one.

    Never overwrites: a file that already exists is kept, so running it again,
    or inside a clone, changes nothing you have edited.
    """
    from policyforge.scaffold import init_project

    try:
        report = init_project(directory)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    for path in report.written:
        click.echo(f"  wrote  {path.as_posix()}")
    for path in report.kept:
        click.echo(f"  kept   {path.as_posix()} (already there)")
    where = "this directory" if directory == Path(".") else str(directory)
    click.echo(f"\nProject ready in {where}. Next:")
    step = 1
    if directory != Path("."):
        click.echo(f"  {step}. cd {directory}")
        step += 1
    click.echo(
        f"  {step}. cp config/config.example.yaml config/config.yaml, and set your model "
        "and the *name* of the variable holding its key"
    )
    click.echo(f"  {step + 1}. policyforge llm-check")

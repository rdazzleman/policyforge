"""Starting a project directory, for an install that is not a clone."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.topics.convergence import CEILING as _CEILING


@cli.command("init")
@click.argument(
    "directory",
    required=False,
    default=Path("."),
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--provider",
    default=None,
    help="The model provider for the starter config.yaml (local, anthropic, gemini, "
    "litellm, or another provider's name with --model). Default: local, a model on "
    "this machine that needs no key.",
)
@click.option("--model", default=None, help="The model for the starter config.yaml.")
@click.option(
    "--api-key-env",
    default=None,
    help="The NAME of the environment variable holding the provider's key. The key "
    "itself is never asked for or written.",
)
@click.option(
    "--builds-software/--no-builds-software",
    default=None,
    help="Scope fact: the organisation builds software (turns Secure Development on). "
    "Left out, it is undeclared and its topic is left out.",
)
@click.option(
    "--own-facilities/--no-own-facilities",
    default=None,
    help="Scope fact: the organisation runs its own facilities (turns Physical & "
    "Environmental Security on).",
)
@click.option(
    "--personal-data",
    type=click.Choice(["none", "pii", "phi"]),
    default=None,
    help="Scope fact: the personal data handled. pii adds Privacy; phi adds the HIPAA "
    "topics as well.",
)
@click.option(
    "--baseline",
    type=click.Choice(["none", "800-53", "fedramp", "govramp"]),
    default=None,
    help="Scope fact: an assessed control baseline, which keeps Personnel Security and "
    "System Maintenance as topics.",
)
@click.option(
    "--ai",
    "ai_role",
    type=click.Choice(["none", "user", "builder"]),
    default=None,
    help="Scope fact: the organisation's role with AI. user gets three AI topics; "
    "builder gets the AI RMF's five, and up to two more.",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Bring an existing project's bundled catalogs and example configs up to this "
    "version and say what changed. Your config.yaml and topics.yaml are not touched, "
    "and a replaced file's previous copy is kept under output/.init-backup/.",
)
def init_cmd(
    directory: Path,
    provider: str | None,
    model: str | None,
    api_key_env: str | None,
    builds_software: bool | None,
    own_facilities: bool | None,
    personal_data: str | None,
    baseline: str | None,
    ai_role: str | None,
    refresh: bool,
):
    """Lay out a PolicyForge project: catalogs, config.yaml, topics.yaml, .gitignore.

    Every command reads `config/` and `data/frameworks/` relative to where it
    runs. A clone of the repository already has them; an installed package
    (Homebrew, pipx, pip) does not, and this writes them. The public-domain
    catalogs are copied in - NIST 800-53, the AI RMF and its Playbook, NIST
    800-171, CSF 2.0, FedRAMP, ARC-AMPE, the HIPAA Security Rule, Privacy Rule
    and Breach Notification Rule, 42 CFR Part 2, Information Blocking and ONC
    170.315 - with a README for each bring-your-own one. It also writes a
    starter `config/config.yaml` (your provider and model, or a local model
    that needs no key, loading 800-53, the AI RMF and the three HIPAA rules)
    and `config/topics.yaml`: the topics the organisation's scope facts call
    for, at most 25, with what was folded away and what was not declared named
    at the top. Declare the facts with --builds-software, --own-facilities,
    --personal-data, --baseline and --ai, or under `programme:` in config.yaml.

    Never overwrites: a file that already exists is kept, so running it again,
    or inside a clone, changes nothing you have edited. After an upgrade,
    `--refresh` replaces the bundled catalogs and examples that changed and
    lists them.
    """
    from policyforge.scaffold import init_project, refresh_project

    if refresh:
        if (
            provider
            or model
            or api_key_env
            or any(
                v is not None
                for v in (builds_software, own_facilities, personal_data, baseline, ai_role)
            )
        ):
            raise click.UsageError(
                "--refresh updates the bundled catalogs and examples only; it doesn't "
                "touch config.yaml or topics.yaml, so --provider, --model, --api-key-env "
                "and the scope-fact options don't apply."
            )
        if not (directory / "data" / "frameworks").is_dir():
            raise click.ClickException(
                f"There is no project in {directory} to refresh (no data/frameworks/). "
                "Run `policyforge init` first."
            )
        try:
            refreshed = refresh_project(directory)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        for path, old, new in refreshed.updated:
            click.echo(f"  updated  {path.as_posix()} (sha256 {old} -> {new})")
        for path in refreshed.added:
            click.echo(f"  added    {path.as_posix()}")
        click.echo(
            f"\n{len(refreshed.updated)} updated, {len(refreshed.added)} added, "
            f"{refreshed.unchanged} unchanged."
        )
        if refreshed.backup is not None:
            click.echo(
                f"The previous copies of what changed are in {refreshed.backup.as_posix()}/."
            )
        return

    from policyforge.topics.scope_facts import ScopeFacts

    facts = ScopeFacts(
        builds_software=builds_software,
        own_facilities=own_facilities,
        personal_data=personal_data,
        baseline=baseline,
        ai=ai_role,
    )
    try:
        report = init_project(
            directory,
            provider=provider or "local",
            model=model,
            api_key_env=api_key_env,
            facts=facts,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    for path in report.written:
        click.echo(f"  wrote  {path.as_posix()}")
    for path in report.kept:
        click.echo(f"  kept   {path.as_posix()} (already there)")
    selection = report.selection
    if selection is not None:
        click.echo(
            f"\nconfig/topics.yaml: {selection.count} topics from the scope facts "
            f"(ceiling {_CEILING})."
        )
        for merge in selection.merges:
            click.echo(f"  folded  {merge.line()}")
        if selection.dropped_extras:
            click.echo("  left out to stay at the ceiling: " + ", ".join(selection.dropped_extras))
        if selection.undeclared:
            click.echo(
                "  not declared, so off: " + ", ".join(selection.undeclared) + ".\n"
                "  Declare them under `programme:` in config/config.yaml (or pass the "
                "options to `policyforge init`), then delete config/topics.yaml and "
                "run `policyforge init` again."
            )
    for note in report.notes:
        click.echo(f"\nNote: {note}")
    where = "this directory" if directory == Path(".") else str(directory)
    click.echo(f"\nProject ready in {where}. Next:")
    step = 1
    if directory != Path("."):
        click.echo(f"  {step}. cd {directory}")
        step += 1
    if report.api_key_env:
        click.echo(
            f"  {step}. export {report.api_key_env}=...  (config/config.yaml names the "
            "variable, never holds the key)"
        )
    else:
        click.echo(
            f"  {step}. start your model server (Ollama: `ollama run "
            "qwen3:14b`), or change `llm:` in config/config.yaml"
        )
    click.echo(f"  {step + 1}. policyforge llm-check")
    click.echo(f"  {step + 2}. policyforge coverage   (config/topics.yaml against the catalogs)")

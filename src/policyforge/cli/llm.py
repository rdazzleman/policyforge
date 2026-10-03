"""Checking the model connection, the content boundary, and the call ledger."""

from __future__ import annotations

from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import (
    get_provider,
    load_config,
    load_config_or_empty,
)
from policyforge.config import resolve_config_path


@cli.command("llm-check")
def llm_check():
    """Confirm your configured API key + model actually work."""
    # Named explicitly because the whole point of POLICYFORGE_CONFIG is
    # running two providers against one working tree, and "which model did
    # that number come from" is the question a comparison has to answer.
    path = resolve_config_path()
    config = load_config()
    provider = get_provider(config)
    ok = provider.check()
    if ok:
        # Asked of the provider rather than read out of config: a cascade
        # has no top-level `model` at all, only two nested blocks, and
        # every provider can already say what it is.
        model = getattr(provider, "model", config["llm"].get("model", "?"))
        click.echo(f"OK — {config['llm']['provider']} / {model} responded (config: {path}).")
        # Where this provider sends its bytes decides what may be sent to
        # it, so it belongs in the one command people run to find out what
        # they are pointed at. `policyforge boundary` has the whole table.
        from policyforge.llm.boundary import classify_provider

        click.echo(f"Boundary class: {classify_provider(config['llm'])}")
        # Asked of the provider as configured — through the ledger wrapper,
        # not around it — because that is the object every command calls.
        # The one time these went dark on every provider, nothing printed
        # them, and the downgrade was invisible for two days.
        from policyforge.llm.base import capabilities

        report = capabilities(provider)
        summary = ", ".join(f"{name} {'yes' if ok else 'no'}" for name, ok in report.items())
        click.echo(f"Capabilities: {summary}")
    else:
        click.echo("Provider responded, but the sanity check didn't match expected output.")
        raise SystemExit(1)


@cli.command("boundary")
@click.option(
    "--path",
    "paths",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Classify these files or directories too, and say whether the configured "
    "provider may receive them. Repeatable.",
)
def boundary_cmd(paths: tuple[Path, ...]):
    """Show what may be sent to the configured model, and why.

    Providers are classified by where the bytes end up - local, self-hosted,
    or a third-party processor - and content by who may hold it. The table is
    the pairing between them, and it is checked before a call rather than
    described in a README.

    Run it to answer "can this configuration work offline" without having to
    remember, and to see a refusal's reasoning before a command hits it.
    """
    from policyforge.llm import boundary

    config = load_config_or_empty()

    provider = boundary.classify_provider(config.get("llm") or {})
    click.echo(f"Configured provider: {provider}")
    click.echo("")
    click.echo(boundary.matrix(config))
    for line in boundary.agreement_lines(config, provider):
        click.echo(line if line.startswith("  ") else f"\n{line}")

    tightened = {
        content_class: ceiling
        for content_class, ceiling in boundary.ceilings(config).items()
        if ceiling != boundary.DEFAULT_CEILINGS[content_class]
    }
    if tightened:
        click.echo("")
        click.echo("Tightened by config (llm.boundary):")
        for content_class, ceiling in tightened.items():
            click.echo(f"  {content_class} -> at most {ceiling}")

    if not paths:
        return

    click.echo("")
    refused = 0
    for path in paths:
        decision = boundary.check(boundary.classify_path(path, config), provider, config)
        refused += not decision.allowed
        click.echo(f"{path}")
        click.echo(f"  {decision.explain()}")

    if refused:
        # Exits non-zero so this can gate a pipeline, not only inform a human.
        raise SystemExit(1)


@cli.command("model-log")
@click.option(
    "--by",
    type=click.Choice(["model", "subject", "site", "provider", "content_class"]),
    default="model",
    show_default=True,
    help="What to group the totals by.",
)
@click.option("--subject", default=None, help="Only calls about this document or control.")
@click.option("--model", "model_filter", default=None, help="Only calls answered by this model.")
@click.option(
    "--since",
    default=None,
    help="Only calls on or after this ISO date, e.g. 2026-09-01.",
)
@click.option(
    "--path",
    "ledger_file",
    default=None,
    type=click.Path(path_type=Path),
    help="Ledger to read. Defaults to `llm.ledger.path`, else output/.model-log/calls.jsonl.",
)
def model_log_cmd(
    by: str,
    subject: str | None,
    model_filter: str | None,
    since: str | None,
    ledger_file: Path | None,
):
    """What was sent to which model, when, and at what cost.

    Every model call this tool makes is recorded - provider, model, the
    document or control it was about, token counts, cost, and a hash of the
    prompt. Not the prompt and not the reply: a record that quoted what it
    saw would copy licensed content into a file, which is the leak it exists
    to disprove.

    This is the answer to "which documents did that model touch", asked the
    day a model turns out to have been weakening the requirements it cited.
    """
    from policyforge.llm import ledger

    config = load_config_or_empty()

    path = ledger_file or ledger.ledger_path(config)
    records = ledger.load(path)
    if not records:
        click.echo(f"No model calls recorded in {path}.")
        if not ledger.ledger_enabled(config):
            click.echo("  `llm.ledger.enabled` is false in config, so nothing is being recorded.")
        return

    total_before = len(records)
    if subject:
        records = [r for r in records if r.subject == subject]
    if model_filter:
        records = [r for r in records if r.model == model_filter]
    if since:
        records = [r for r in records if r.timestamp >= since]

    if not records:
        click.echo(f"None of the {total_before} recorded call(s) match those filters.")
        return

    click.echo(f"{len(records)} of {total_before} recorded call(s), by {by}:")
    click.echo(ledger.format_summary(records, by))

    overall = ledger.Totals()
    for record in records:
        overall.add(record)
    click.echo("")
    click.echo(
        f"Total: {overall.calls} call(s), {overall.input_tokens} in / "
        f"{overall.output_tokens} out, {overall.cost}"
    )
    if overall.errors:
        # Said out loud: a failed call still reached the vendor, was still
        # billed, and still carried its content there.
        click.echo(f"  {overall.errors} call(s) failed after being sent.")
    click.echo(f"  {path}")

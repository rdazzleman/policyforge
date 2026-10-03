"""An organization's own crosswalk: seed it, check it, propose and review changes."""

from __future__ import annotations

import re
from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import get_provider, load_config_or_empty

_DEFAULT_CONTROLS = (
    Path("data/frameworks/nist-800-53-r5/controls.json"),
    Path("data/frameworks/hipaa-security-rule/controls.json"),
)


def _published_catalogs(paths) -> list:
    """The catalogs as published, with no overlay applied.

    Seeding and checking compare an overlay against what the catalog itself
    says, so they must not read through the overlay they are about.
    """
    from policyforge.ingest.schema import load_controls

    controls: list = []
    for path in paths:
        controls.extend(load_controls(path))
    return controls


def _refuse_bundled_destination(path: Path) -> None:
    """An overlay may hold an organization's decisions and, from a licensed
    catalog, its digests; neither belongs in this project's bundled public
    catalogs, which are committed and pushed. The same test `etl-*` uses."""
    from policyforge.cli.etl import _lands_in_bundled_catalogs, _names_a_bundled_catalog_directory

    if _names_a_bundled_catalog_directory(path) or _lands_in_bundled_catalogs(path):
        raise click.ClickException(
            f"{path} is inside data/frameworks/, this project's bundled public content. "
            "Keep overlays in config/crosswalks/."
        )


def _write(path: Path, text: str) -> None:
    from policyforge.textfile import write_text_lf

    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(path, text)


def _framework_slug(framework: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", framework.lower()).strip("-")


_controls_option = click.option(
    "--controls",
    "controls_paths",
    multiple=True,
    default=_DEFAULT_CONTROLS,
    type=click.Path(exists=True, path_type=Path),
    help="A controls.json. Repeatable; pass the 800-53 catalog and the mapped framework's.",
)


@cli.group("crosswalk")
def crosswalk_group():
    """Your organization's reviewed mapping of a framework onto 800-53.

    Published crosswalks are pairs with no stated relationship or reasoning.
    An overlay in config/crosswalks/ records which pairs your organization
    accepts, rejects or has added, and only accepted pairs reach `map`,
    `synthesize`, `coverage`, `bundle` and Zardoz.
    """


@crosswalk_group.command("seed")
@click.option(
    "--framework",
    default="HIPAA Security Rule",
    show_default=True,
    help="The framework to map, as its catalog names it.",
)
@_controls_option
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write the overlay (default: config/crosswalks/<framework>.yaml).",
)
@click.option("--force", is_flag=True, help="Overwrite an existing overlay.")
def crosswalk_seed(framework: str, controls_paths, out: Path | None, force: bool):
    """Write an overlay holding the published mapping, every pair accepted.

    Changes nothing on its own: the pipeline reads the same pairs it did
    before. It is the file you then review: reject a pair by changing its
    status, add one with a new row, and record why.
    """
    from policyforge.crosswalk.overlay import (
        DEFAULT_OVERLAY_DIR,
        OverlayError,
        dump_overlay,
        seed_overlay,
    )

    out = out or DEFAULT_OVERLAY_DIR / f"{_framework_slug(framework)}.yaml"
    _refuse_bundled_destination(out)
    if out.exists() and not force:
        raise click.ClickException(
            f"{out} already exists, and it may hold decisions somebody made. "
            "Pass --force to replace it with the published mapping."
        )
    try:
        overlay = seed_overlay(_published_catalogs(controls_paths), framework)
    except OverlayError as exc:
        raise click.ClickException(str(exc)) from exc

    _write(out, dump_overlay(overlay))
    pairs = sum(len(rows) for rows in overlay.requirements.values())
    unmapped = sum(1 for rows in overlay.requirements.values() if not rows)
    click.echo(
        f"Wrote {len(overlay.requirements)} {framework} requirements ({pairs} published "
        f"pairs, {unmapped} with none) -> {out}"
    )


@crosswalk_group.command("check")
@_controls_option
@click.option(
    "--overlays",
    "overlay_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory of overlays (default: config/crosswalks).",
)
@click.option("--strict", is_flag=True, help="Exit non-zero when anything needs attention.")
def crosswalk_check(controls_paths, overlay_dir: Path | None, strict: bool):
    """Report what in each overlay no longer matches the catalogs."""
    from policyforge.crosswalk.overlay import (
        DEFAULT_OVERLAY_DIR,
        OverlayError,
        check_overlay,
        load_overlays,
    )

    try:
        overlays = load_overlays(overlay_dir or DEFAULT_OVERLAY_DIR)
    except OverlayError as exc:
        raise click.ClickException(str(exc)) from exc
    if not overlays:
        click.echo(
            f"No overlays in {overlay_dir or DEFAULT_OVERLAY_DIR}. Every framework uses its "
            "published mapping. Start one with `policyforge crosswalk seed`."
        )
        return

    controls = _published_catalogs(controls_paths)
    attention = False
    for overlay in overlays:
        check = check_overlay(overlay, controls)
        click.echo(f"{overlay.path}: {overlay.framework}")
        click.echo(
            f"  {len(overlay.requirements)} requirements, {check.proposed} pair(s) needing review"
        )
        for rid in check.unknown_requirements:
            click.echo(
                f"  unknown requirement: {rid} - not in the loaded {overlay.framework} catalog"
            )
        for rid, control in check.unknown_controls:
            click.echo(f"  unknown control: {rid} -> {control} - not in the loaded catalogs")
        for rid, control in check.unreviewed_published:
            click.echo(
                f"  unreviewed: {rid} -> {control} is published but the overlay neither "
                "accepts nor rejects it"
            )
        if check.is_clean:
            click.echo("  matches the catalogs")
        attention = attention or not check.is_clean
    if strict and attention:
        raise SystemExit(1)


def _overlay_path(framework: str, overlay: Path | None) -> Path:
    from policyforge.crosswalk.overlay import DEFAULT_OVERLAY_DIR

    path = overlay or DEFAULT_OVERLAY_DIR / f"{_framework_slug(framework)}.yaml"
    _refuse_bundled_destination(path)
    return path


def _open_overlay(path: Path, framework: str, controls):
    from policyforge.crosswalk.overlay import OverlayError, load_overlay, seed_overlay

    try:
        if path.exists():
            overlay = load_overlay(path)
            if overlay.framework.casefold() != framework.casefold():
                raise click.ClickException(
                    f"{path} maps {overlay.framework!r}, not {framework!r}. Pass --overlay."
                )
            return overlay
        overlay = seed_overlay(controls, framework)
    except OverlayError as exc:
        raise click.ClickException(str(exc)) from exc
    overlay.path = path
    return overlay


def _save(overlay) -> None:
    from policyforge.crosswalk.overlay import dump_overlay

    _write(overlay.path, dump_overlay(overlay))


@crosswalk_group.command("propose")
@click.option("--framework", default="HIPAA Security Rule", show_default=True)
@_controls_option
@click.option(
    "--overlay",
    type=click.Path(path_type=Path),
    default=None,
    help="The overlay to record proposals in (default: config/crosswalks/<framework>.yaml; "
    "seeded from the published mapping when it does not exist).",
)
@click.option("--only", multiple=True, help="Propose for this requirement id only. Repeatable.")
def crosswalk_propose(framework: str, controls_paths, overlay: Path | None, only):
    """Ask the configured model to read each requirement against 800-53.

    One call per requirement. Nothing it says is a decision: a published
    pair it confirms gains the quotes that justify it, one it could not
    justify is flagged but stays accepted, and a pair it adds is proposed.
    Run `policyforge crosswalk review` to decide them.
    """
    from policyforge.cli.documents import _enforce_catalogs, _most_restrictive
    from policyforge.crosswalk.candidates import WordIndex, catalog_entries
    from policyforge.crosswalk.overlay import published_pairs
    from policyforge.crosswalk.propose import MergeReport, merge, propose_for, requirements_of
    from policyforge.llm import ledger

    config = load_config_or_empty()
    # Catalog text is sent to the provider, so a catalog that may not leave is
    # refused here exactly as `synthesize` refuses it.
    classified = _enforce_catalogs(controls_paths, config)
    # The overlay is a file in the organization's repository. Quotes from a
    # licensed catalog would be licensed text in it, so they are verified and
    # then recorded as digests only.
    from policyforge.llm.boundary import LICENSED

    keep_quotes = _most_restrictive(classified) != LICENSED
    controls = _published_catalogs(controls_paths)
    requirements = requirements_of(controls, framework)
    if only:
        wanted = set(only)
        requirements = [r for r in requirements if r.requirement_id in wanted]
        missing = wanted - {r.requirement_id for r in requirements}
        if missing:
            raise click.UsageError(f"Not {framework} requirements: {', '.join(sorted(missing))}")
    if not requirements:
        raise click.UsageError(f"No {framework} requirements in the loaded catalogs.")
    entries = catalog_entries(controls)
    if not entries:
        raise click.UsageError("No 800-53 catalog among --controls to map onto.")

    provider = get_provider(config)
    if not getattr(provider, "supports_schema", lambda: False)():
        raise click.ClickException(
            "The configured provider cannot hold a reply to a schema, and a mapping read "
            "out of free prose is not one to put in front of a reviewer."
        )
    model = str((config.get("llm") or {}).get("model") or getattr(provider, "model", "unknown"))

    path = _overlay_path(framework, overlay)
    record = _open_overlay(path, framework, controls)
    published = published_pairs(controls, framework)
    index = WordIndex(entries)
    total = MergeReport()

    with (
        ledger.run(report=click.echo),
        ledger.about(  # #379
            f"crosswalk/{_framework_slug(framework)}",
            site="crosswalk-propose",
            content_class=_most_restrictive(classified),
        ),
    ):
        for number, requirement in enumerate(requirements, start=1):
            proposal = propose_for(
                requirement,
                framework=framework,
                published=published.get(requirement.requirement_id, []),
                entries=entries,
                index=index,
                provider=provider,
            )
            step = merge(record, [proposal], controls, model=model, keep_quotes=keep_quotes)
            # Written after every requirement, so an interrupted run keeps what
            # it paid for.
            _save(record)
            total.confirmed += step.confirmed
            total.not_confirmed += step.not_confirmed
            total.proposed += step.proposed
            total.left_reviewed += step.left_reviewed
            total.rejected_again += step.rejected_again
            total.errors.extend(step.errors)
            total.truncated.extend(step.truncated)
            refused = ""
            if proposal.refused:
                why = "unverifiable quotes"
                if proposal.nameless:
                    why = f"{proposal.nameless} naming no candidate control, rest on quotes"
                refused = f", {len(proposal.refused)} refused ({why})"
            if proposal.error:
                outcome = f"error: {proposal.error}{refused}"
            else:
                outcome = (
                    f"{step.confirmed} confirmed, {step.not_confirmed} not confirmed, "
                    f"{step.proposed} new"
                )
                outcome += refused
            click.echo(f"[{number}/{len(requirements)}] {requirement.requirement_id}: {outcome}")

    click.echo(
        f"\n{total.confirmed} published pair(s) confirmed with quotes, {total.not_confirmed} "
        f"flagged as not confirmed, {total.proposed} new pair(s) proposed -> {path}"
    )
    if total.left_reviewed:
        click.echo(f"{total.left_reviewed} reviewed row(s) left unchanged.")
    if total.errors:
        click.echo(f"{len(total.errors)} requirement(s) failed and were left as they were:")
        for line in total.errors:
            click.echo(f"  {line}")
    if total.truncated:
        click.echo(
            f"{len(total.truncated)} of those were cut off at the model's output budget: "
            + ", ".join(total.truncated)
            + "\n  Re-run those with `--only`, or raise the budget, before relying on the "
            "overlay: they have no proposal at all, not a partial one."
        )
    click.echo("Nothing reaches the pipeline until reviewed: policyforge crosswalk review")
    if total.errors:
        # Non-zero because a requirement nobody proposed for is a gap in the
        # review queue, and a script that ran this must be able to see it.
        raise SystemExit(1)


def _reviewer() -> str:
    import getpass
    import subprocess  # nosec B404 - asks git for the configured name, nothing else

    from policyforge.child_output import strict_text

    argv = ["git", "config", "user.name"]
    try:
        result = subprocess.run(argv, capture_output=True, check=False, timeout=5)  # nosec B603 B607
    except (OSError, subprocess.TimeoutExpired):
        return getpass.getuser()
    # DATA: recorded in the overlay as who decided. Bytes, decoded here, so a
    # name that is not UTF-8 refuses instead of being recorded as `JosÃ©`,
    # which is what a cp1252 decode of "José" wrote before #287.
    name = strict_text(result.stdout, site="crosswalk review (reviewer name)", argv=argv).strip()
    return name or getpass.getuser()


@crosswalk_group.command("review")
@click.option("--framework", default="HIPAA Security Rule", show_default=True)
@_controls_option
@click.option("--overlay", type=click.Path(path_type=Path), default=None)
@click.option("--who", default=None, help="Recorded as the reviewer (default: git user.name).")
def crosswalk_review(framework: str, controls_paths, overlay: Path | None, who: str | None):
    """Decide, one at a time, each pair a model flagged or proposed.

    Flagged published pairs come first (the ones a model could not find a
    basis for), then new proposals. Each decision is written as it is made,
    with who made it and when. Accept keeps or adds the pair; reject removes
    it from everything built on this crosswalk.
    """
    from datetime import date

    from policyforge.crosswalk.candidates import catalog_entries
    from policyforge.crosswalk.overlay import ACCEPTED, REJECTED, RELATIONSHIPS, printable
    from policyforge.crosswalk.propose import requirements_of

    path = _overlay_path(framework, overlay)
    if not path.exists():
        raise click.ClickException(
            f"No overlay at {path}. Run `policyforge crosswalk propose` first."
        )
    controls = _published_catalogs(controls_paths)
    record = _open_overlay(path, framework, controls)
    entries = catalog_entries(controls)
    requirements = {r.requirement_id: r for r in requirements_of(controls, framework)}

    queue = [
        (rid, row) for rid, rows in record.requirements.items() for row in rows if row.needs_review
    ]
    queue.sort(key=lambda item: (not item[1].flags, item[0]))
    if not queue:
        click.echo(f"Nothing in {path} needs review.")
        return

    reviewer = who or _reviewer()
    decided = 0
    for position, (rid, row) in enumerate(queue, start=1):
        requirement = requirements.get(rid)
        entry = entries.get(row.control)
        # Everything shown is passed through `printable`: catalog text and
        # model quotes alike reach a terminal here.
        say = lambda text: click.echo(printable(text))  # noqa: E731
        click.echo(f"\n[{position}/{len(queue)}] {rid} -> {row.control}   ({row.status})")
        if requirement is not None:
            if requirement.parent:
                say(f"  under: {requirement.parent[:160]}")
            say(f"  requirement: {requirement.title} - {requirement.text[:300]}")
        if entry is not None:
            say(f"  control: {entry.title} - {entry.text[:300]}")
        click.echo(f"  published: {'yes' if 'published' in row.sources else 'no'}")
        if row.flags:
            say(f"  flags: {', '.join(row.flags)}")
        click.echo(f"  relationship: {row.relationship}")
        if row.proposed_relationship:
            click.echo(f"  model suggests: {row.proposed_relationship}")
        if row.evidence:
            say(f'  requirement says: "{row.evidence.get("requirement", "")}"')
            say(f'  control says:     "{row.evidence.get("control", "")}"')
        choice = click.prompt(
            "  [a]ccept, [r]eject, [s]kip, [q]uit",
            type=click.Choice(["a", "r", "s", "q"]),
            show_choices=False,
        )
        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "a":
            # The relationship is decided here, by the person accepting, and
            # only here: coverage reads it.
            suggested = row.proposed_relationship or row.relationship
            row.relationship = click.prompt(
                "  relationship",
                type=click.Choice(list(RELATIONSHIPS)),
                default=suggested,
                show_choices=True,
            )
        why = click.prompt("  why (optional)", default="", show_default=False)
        row.status = ACCEPTED if choice == "a" else REJECTED
        row.flags = []
        row.proposed_relationship = ""
        if why:
            row.rationale = printable(why)
        row.reviewed_by = {"who": reviewer, "date": date.today().isoformat()}
        _save(record)
        decided += 1

    remaining = sum(1 for rows in record.requirements.values() for r in rows if r.needs_review)
    click.echo(f"\n{decided} decision(s) recorded in {path}; {remaining} still need review.")

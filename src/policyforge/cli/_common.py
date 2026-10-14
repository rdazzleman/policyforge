"""Helpers more than one command group needs, and the config/provider seam."""

from __future__ import annotations

import re
from pathlib import Path

import click

# Local, offline version history of generated/imported documents — see
# history/version_store.py's module docstring for what this is (and isn't)
# a substitute for. Shared default across `generate`, `import-confluence`,
# and `history` so a given tier+name lands in the same stream by default.
_DEFAULT_HISTORY_DIR = Path("output/.history")


class _RetiredForce(click.ClickException):
    """Exit 2, one line. A `UsageError` raised while click parses gets the
    usage block attached, four lines for a one-line answer."""

    exit_code = 2


def retired_force(flag: str, to: str):
    """A hidden `--force` that refuses, for a command where it used to mean
    something other than "overwrite my file" (#372, 80's ruling).

    `--force` meant three things: overwrite a local file (`crosswalk seed`,
    `generate-parser`, which keep it), overwrite someone else's edit on the
    wiki (`publish`), and write a licensed catalog without the repository's
    declaration (the licensed `etl-*` commands). Those two now take `flag`.
    Here `--force` is still recognised, so a script that relied on it stops
    loudly instead of doing either thing silently: one line, exit 2, naming
    `flag`, before anything runs (eager), and it never performs the old
    action. Hidden from `--help`. `to` completes "to ...": what the new flag
    does, in the words of its help.
    """

    def refuse(ctx, param, value):
        if value:
            raise _RetiredForce(f"`--force` no longer applies here: {to}, pass `{flag}`.")
        return value

    return click.option(
        "--force",
        "retired_force",
        is_flag=True,
        hidden=True,
        is_eager=True,
        expose_value=False,
        callback=refuse,
    )


#: What a document name may contain. The same shape `slugify` produces, so
#: a name that came from this tool round-trips and a name somebody typed is
#: held to what this tool would have written.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _checked_slug(name: str) -> str:
    """`name`, or a usage error — it is about to become a path segment.

    `history` and `import-confluence` build their storage path as
    `history_dir / tier / name`, and every other path in this project goes
    through `slugify` first. These two did not, so `--name ../../../../tmp/x`
    resolved outside the history store entirely (verified: it wrote to
    `C:\\tmp\\pwned\\index.jsonl`), and `--name ..\\..\\evil` climbed out of
    `.history` into `output/`.

    This is a local CLI, so the realistic case is a mistake rather than an
    attacker — a name pasted with a stray path on the front, quietly writing
    a version stream somewhere nobody will look for it again. Rejecting is
    better than slugifying silently: a name that is not what this tool would
    have written is a name the caller should see corrected, not have guessed
    at, because the corrected form is the one they must type next time to
    read the history back.
    """
    if not _SLUG_RE.match(name):
        raise click.UsageError(
            f"--name must be a slug — lowercase letters, digits and single hyphens "
            f"— and {name!r} is not. It becomes a directory name under the history "
            f"store, so a name carrying path separators would write outside it. "
            f"Try {(re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'access-control')!r}."
        )
    return name


def _content_dir(override: Path | None) -> Path:
    """The markdown content tree: the flag, then config, then `docs/`.

    Shared by generate, publish, pull, check and `zardoz sync`, because they
    are all talking about the same tree and a repo that had to say where it
    lives four times would eventually say it differently once. The order is
    written once, in `zardoz.corpus.content_tree`, which takes the config it
    is handed; this loads the project's.
    """
    from policyforge.zardoz.corpus import content_tree

    return content_tree(None if override is not None else load_config_or_empty(), override)


def max_cost_option(command):
    """`--max-cost USD`: a spend ceiling for this run, over `budget.max_usd`
    (#125). On every command that can make more than one paid call."""
    return click.option(
        "--max-cost",
        "max_cost",
        type=float,
        default=None,
        help="Refuse a run estimated to cost more than this many dollars, and stop one "
        "whose cost passes it. Overrides `budget.max_usd` in config for this run.",
    )(command)


def budgeted(config: dict, max_cost: float | None) -> dict:
    """`config` with `--max-cost` applied, or a usage error for a bad value."""
    from policyforge.llm import spend

    try:
        return spend.with_max_cost(config, max_cost)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


def plan_spend(config: dict, *, calls: int, input_tokens: int, max_tokens: int) -> str | None:
    """Refuse before the first request when a run that makes `calls` requests
    could cost more than the ceiling (#125, `SpendRefused`, exit 2).

    Returns one line to show where the run is announced, or None when no
    ceiling is set (nothing is said, and nothing changes). The model is the
    one config names; its class is the provider's, from the boundary.
    """
    from policyforge.llm import spend
    from policyforge.llm.boundary import classify_provider

    ceiling = spend.ceiling_of(config)
    if ceiling is None:
        return None
    llm = config.get("llm") or {}
    planned = spend.estimate(
        model=str(llm.get("model") or "(no model named)"),
        provider_class=classify_provider(llm).klass,
        calls=calls,
        input_tokens=input_tokens,
        max_tokens=max_tokens,
    )
    spend.refuse_if_over(planned, ceiling)
    bound = planned.usd
    if planned.rates.free:
        return f"Spend ceiling {spend.usd(ceiling)}: this model has no per-token charge."
    return (
        f"Spend ceiling {spend.usd(ceiling)}: estimated at most {spend.usd(bound)} for {calls} "
        f"request(s), an upper bound with every output priced at its token limit."
    )


class RegistryPath(click.Path):
    """`--topics`: a missing registry says what to do next (#176) instead of
    click's bare "Path does not exist"."""

    def convert(self, value, param, ctx):
        try:
            return super().convert(value, param, ctx)
        except click.BadParameter:
            if value is not None and not Path(str(value)).exists():
                self.fail(
                    f"No topic registry at {value}. Run `policyforge init` to write a "
                    "starter one, or pass --topics.",
                    param,
                    ctx,
                )
            raise


def controls_option(help_text: str, *, fallback: str = ""):
    """`--controls`, for a command whose catalog set is the programme's.

    Not required: without it `programme.catalogs` in config names the set
    (`resolve_catalogs`), and a command that defaulted to 800-53 keeps that
    only where no profile exists (#176): `fallback` names that default in the
    option's help.
    """
    return click.option(
        "--controls",
        "controls_paths",
        multiple=True,
        default=None,
        type=click.Path(exists=True, path_type=Path),
        help=f"{help_text} Without it, `programme.catalogs` in config names the set"
        + (f"; with none, {fallback}." if fallback else "."),
    )


def resolve_catalogs(given, *, default: tuple[Path, ...] = (), config: dict | None = None):
    """The catalog files a command runs against (80's spec on #176).

    1. `--controls` given: exactly those. The profile is not merged in.
    2. Otherwise `programme.catalogs`: each entry a framework NAME (the
       registry's id, bundled or under `local_content/`) or a path to a
       controls.json. One line says which set is in use.
    3. Otherwise the command's own `default`, said so in one line.
    4. Otherwise one line, exit 2, naming the key and the flag.

    An unknown name or a missing path refuses, naming it, before anything
    runs. The profile only names files: every boundary check a licensed
    catalog has is made by the command, as it is for `--controls`.
    """
    if given:
        return list(given)
    config = load_config_or_empty() if config is None else config
    profile = config.get("programme")
    names = profile.get("catalogs") if isinstance(profile, dict) else None
    if profile is not None and not isinstance(profile, dict):
        raise click.UsageError("`programme:` in config must be a mapping holding `catalogs:`.")
    if names is not None and not (
        isinstance(names, list) and names and all(isinstance(n, str) and n.strip() for n in names)
    ):
        raise click.UsageError(
            "`programme.catalogs` must be a list of framework names or controls.json paths."
        )
    if names:
        resolved = [_catalog_file(name.strip(), config) for name in names]
        # On stderr: a status line about the run, not its output. `coverage
        # --json` and `satisfies --json` print a document on stdout.
        click.echo(
            f"catalogs: programme ({len(resolved)}): {', '.join(n.strip() for n in names)}",
            err=True,
        )
        return resolved
    if default:
        missing = [p for p in default if not Path(p).exists()]
        if missing:
            raise click.UsageError(
                f"No `programme.catalogs` is set, and the default {missing[0]} is not here. "
                "Pass --controls, or set `programme.catalogs` in config/config.yaml."
            )
        click.echo(
            f"catalogs: no programme.catalogs set, so the default ({len(default)}): "
            + ", ".join(Path(p).parent.name for p in default),
            err=True,
        )
        return [Path(p) for p in default]
    raise click.UsageError(
        "No catalogs named. Pass --controls, or set `programme.catalogs` in config/config.yaml."
    )


def _catalog_file(name: str, config: dict) -> Path:
    """One `programme.catalogs` entry as a controls.json that exists."""
    from policyforge.frameworks.registry import catalogs_by_id

    named = catalogs_by_id(config).get(name)
    if named is not None:
        if not named.has_controls:
            raise click.UsageError(
                f"`programme.catalogs` names {name!r}, which has no controls.json "
                f"under {named.path}."
            )
        return named.controls_path
    path = Path(name)
    if path.is_file():
        return path
    raise click.UsageError(
        f"`programme.catalogs` names {name!r}, which is not a framework this project "
        "has (see `policyforge frameworks`) and not a file that exists."
    )


def _zardoz_setting(config: dict, key: str, override: str) -> str:
    """CLI flag beats config file beats nothing.

    Host and supporting space live in config because they are properties of
    the organization rather than of one invocation — you type them once, not
    every time you open the shell.
    """
    if override:
        return override
    return str((config.get("zardoz") or {}).get(key) or "")


# ---- the config and provider seam -----------------------------------------
#
# Every command reads config and builds a provider through these two, never by
# importing `policyforge.config` or `policyforge.llm.base` itself. They resolve
# `policyforge.cli.load_config` and `policyforge.cli.get_provider` at call time.
#
# That is what kept the split from being a silent hazard. Fifty-eight test
# sites substitute a fake config and a fake provider by patching those two
# names on `policyforge.cli`. A command that imported the real functions into
# its own module would stop seeing the patch, and a test that believed it was
# talking to a fake would build a real provider instead. One place to
# substitute, looked up when it is used, keeps the tests' contract true for
# every command wherever it lives.


def load_config(*args, **kwargs):
    import policyforge.cli as package

    return package.load_config(*args, **kwargs)


def get_provider(*args, **kwargs):
    import policyforge.cli as package

    return package.get_provider(*args, **kwargs)


def load_config_or_empty() -> dict:
    """The project config, or `{}` when there is no config file.

    Twelve commands carried this as the same four lines. Most of what this
    tool does works without a config file — analyses over the bundled
    catalogs, the shell, the boundary report — and "no config" is a
    supported way to run rather than an error. Named once so that is a
    decision you can read, not a pattern you have to recognise twelve times.

    Only a *missing* file is tolerated. A config that exists and does not
    parse still raises, because silently running with no settings when the
    operator wrote some is how a boundary gets skipped.

    Goes through the seam above, so a test that patches
    `policyforge.cli.load_config` reaches every command using this.
    """
    try:
        return load_config()
    except FileNotFoundError:
        return {}


def load_catalogs(paths) -> list:
    """Every control in every catalog at `paths`, in the order given.

    Six commands carried this loop. Order is preserved because callers
    depend on it: a crosswalk and a coverage report are built from whichever
    catalog names a control first.

    The organization's crosswalk overlays (`config/crosswalks/*.yaml`) are
    applied here, once, so every command that builds a crosswalk sees the
    mappings the organization accepted rather than the published ones. An
    overlay that cannot be read stops the command: running on the published
    mapping when a reviewed one exists would be a quiet wrong answer.
    """
    from policyforge.crosswalk.overlay import OverlayError, apply_overlays, load_overlays
    from policyforge.ingest.schema import load_controls

    controls: list = []
    for path in paths:
        controls.extend(load_controls(path))
    try:
        apply_overlays(controls, load_overlays())
    except OverlayError as exc:
        raise click.ClickException(str(exc)) from exc
    return controls

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

    Shared by publish/pull/check and by `zardoz sync`, because they are all
    talking about the same tree and a repo that had to say where it lives
    four times would eventually say it differently once.
    """
    if override is not None:
        return override
    config = load_config_or_empty()
    configured = (config.get("zardoz") or {}).get("content_dir") or ""
    return Path(configured) if configured else Path("docs")


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

"""PolicyForge's command line, one command group per module.

This was a single 3,600-line `cli.py` holding thirty-four commands, each
carrying its own copy of config loading, catalog loading and the licensed-
write guard. It is now a package split by what the commands are for:

    etl        fetching framework catalogs
    llm        the model connection, the content boundary, the call ledger
    programme  ownership, coverage, parameters, drift — no model involved
    documents  drafting with a model
    content    the content tree and Confluence
    crosswalk  an organization's reviewed mapping of a framework onto 800-53
    zardoz     the shell, discovery, corpus sync, the MCP server
    project    `init`, laying out a project for an installed package

The split was a pure move. Every definition was copied byte for byte, and the
command surface — every command, option, default and help string — was pinned
in tests/fixtures/cli_surface.json before a line moved and compared after.

Two names stay here because code outside the package depends on them being
here. `cli` is the entry point pyproject.toml installs. `load_config` and
`get_provider` are the seam every command reaches through `_common`, so a test
that patches them on this module reaches every command.
"""

from __future__ import annotations

import click

from policyforge.child_output import UndecodableOutput
from policyforge.config import load_config
from policyforge.llm.base import EmptyReply, ProviderRejected, TruncatedResponse, get_provider


class _Group(click.Group):
    """The command group, with one refusal every command inherits.

    A model reply cut off at its output budget is refused by `llm/effort.py`
    after one retry, as `TruncatedResponse`. Translated here, once, into a
    clean non-zero exit that names the document and the budget — so a new
    command that drafts something is covered the day it is written, rather
    than the day somebody notices its traceback. Every writer runs its model
    calls before its file writes, so nothing partial is on disk when this
    fires; `tests/test_truncation.py` holds the writers to that.

    `UndecodableOutput` is the same kind of refusal (#287): a child wrote
    bytes that are not UTF-8 where the output is data, and the message names
    the site, the command and the byte. It reached the user as a traceback
    with that message at the bottom until 1d measured it on #294.
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (TruncatedResponse, EmptyReply, ProviderRejected, UndecodableOutput) as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_Group)
def cli():
    """PolicyForge - cross-mapped compliance policy/procedure generation."""


# Registering the commands. Imported after `cli` exists, because each module
# decorates its commands with it.
from policyforge.cli import (  # noqa: E402
    content,
    crosswalk,
    documents,
    etl,
    llm,
    programme,
    project,
    zardoz,
)
from policyforge.cli._common import _checked_slug  # noqa: E402
from policyforge.cli.etl import etl_vault  # noqa: E402

# `etl_vault` and `_checked_slug` are imported from here by
# scripts/vault_to_data_etl.py and the tests; the command modules are listed so
# `policyforge.cli.content` and the rest resolve as attributes.
__all__ = [
    "_checked_slug",
    "cli",
    "content",
    "crosswalk",
    "documents",
    "etl",
    "etl_vault",
    "get_provider",
    "llm",
    "load_config",
    "programme",
    "project",
    "zardoz",
]

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
import requests

from policyforge.child_output import UndecodableOutput
from policyforge.config import ConfigError, load_config
from policyforge.export.confluence_importer import PageTooDeepError
from policyforge.llm.base import (
    EmptyReply,
    ProviderHttpError,
    ProviderRejected,
    TruncatedResponse,
    get_provider,
)
from policyforge.llm.spend import SpendRefused, SpendStopped
from policyforge.parameters.ledger import LedgerError


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

    `LedgerError` too (issue 190): a parameters ledger holding a key twice
    names the key and both lines, instead of keeping the second silently.

    A spend ceiling (issue 125): `SpendRefused` is a usage error (exit 2,
    before any request), and `SpendStopped` is a clean exit 1 that says what
    was REQUESTED and what was COMPLETED.
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (ConfigError, PageTooDeepError, SpendRefused) as exc:
            # One line and exit 2 (#254). A page too deep to convert (#273)
            # is the same kind of answer: the command can't take this input,
            # and a Usage block from inside the command would say it was typed
            # wrong.
            raise click.UsageError(str(exc)) from exc
        except (requests.exceptions.RequestException, ProviderHttpError) as exc:
            # An HTTP failure is an expected one (#176): the host is wrong, the
            # network is down, the token is refused. One line and what to check,
            # not a traceback from inside `requests`.
            raise click.ClickException(_http_failure(exc)) from exc
        except (
            TruncatedResponse,
            EmptyReply,
            ProviderRejected,
            SpendStopped,
            UndecodableOutput,
            LedgerError,
        ) as exc:
            raise click.ClickException(str(exc)) from exc


def _http_failure(exc: BaseException) -> str:
    """One line for a failed HTTP request, with what to check. The address is
    named without its credentials or query: a token has been seen in both.

    A `requests` error carries its response; a `ProviderHttpError` (what the
    hosted model providers raise for a 401, 403, 429 or 5xx) carries the
    status and address itself. The vendor's body is never put in the line: a
    refused-key reply can echo part of the key."""
    from urllib.parse import urlsplit

    response = getattr(exc, "response", None)
    request = getattr(exc, "request", None)
    raw = (
        getattr(response, "url", None)
        or getattr(request, "url", None)
        or getattr(exc, "url", None)
        or ""
    )
    parts = urlsplit(str(raw))
    try:
        port = parts.port
    except ValueError:
        port = None
    port_part = f":{port}" if port is not None else ""
    # `hostname` drops an IPv6 literal's brackets, and `::1:11434` is not the
    # address `[::1]:11434` names (b5 on 425, measured with Ollama on `[::1]`).
    host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname
    where = f"{parts.scheme}://{host}{port_part}{parts.path}" if parts.hostname else "the server"
    from policyforge.http_service import CONFLUENCE, LLM, service_of

    service = service_of(exc)
    status = getattr(response, "status_code", None) or getattr(exc, "status", None)
    if status:
        # The service is the one the code that made the request named (#413),
        # never read from the status code: a 401 from the model provider is
        # about its key, and a 401 from Confluence about its token.
        if service is None:
            who = ""
            if status in (401, 403):
                advice = "It refused the credential: check the one for that service."
            elif status == 404:
                advice = "Check the address."
            elif status == 429 or status >= 500:
                advice = "The server refused or failed the request; try again later."
            else:
                advice = "Check the host and the request's arguments."
        else:
            who = f" ({service.name})"
            variable = f" in ${service.credential}" if service.credential else ""
            if service.kind == LLM:
                if status in (401, 403):
                    named = variable or " (`llm.api_key_env` names its variable)"
                    advice = f"Check the API key{named}, and that the key has access to the model."
                elif status == 404:
                    advice = (
                        "It doesn't know that model or endpoint: check `llm.model` and "
                        "`llm.base_url`."
                    )
                elif status == 429 or status >= 500:
                    advice = "It refused or failed the request; try again later."
                else:
                    advice = "Check `llm:` in config/config.yaml."
            elif service.kind == CONFLUENCE:
                if status in (401, 403):
                    advice = (
                        f"Check the username and the API token{variable}, and that the "
                        "account can see the space."
                    )
                elif status == 404:
                    advice = "Check the host, and the page's space and title."
                elif status == 429 or status >= 500:
                    advice = "It refused or failed the request; try again later."
                else:
                    advice = "Check the host and the request's arguments."
            else:
                advice = "Check that service's address and credential."
        reason = str(getattr(response, "reason", "") or getattr(exc, "reason", "") or "").strip()
        return f"{status} {reason} from {where}{who}. {advice}".replace("  ", " ")
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{where} did not answer in time. Check the host and your network, then run again."
    return (
        f"Could not reach {where} ({type(exc).__name__}). Check the host and your network, "
        "then run again."
    )


def _print_version(ctx: click.Context, _param, value: bool) -> None:
    """`policyforge --version`: the version pip or brew installed.

    Read from the installed distribution's metadata, never from a string
    typed here, so it is what the package manager put on the machine. Two
    cases say what they are instead of printing a guess:

    - **not installed** (the source run by path, with no distribution to
      ask): said on stderr, exit 1, and no version printed;
    - **installed, but the running code declares another version**
      (`policyforge.__version__`): an editable install made before a bump
      reports its old metadata while newer code runs. Measured in the
      shared `.venv` on 2026-10-04: metadata 1.0.0, source 1.7.0. The
      installed version still goes to stdout, unchanged, and a note naming
      both goes to stderr.
    """
    if not value or ctx.resilient_parsing:
        return
    from importlib.metadata import PackageNotFoundError, version

    import policyforge

    try:
        installed = version("policyforge")
    except PackageNotFoundError:
        click.echo(
            "Not installed as a package, so there is no installed PolicyForge version "
            f"to report. The source at {policyforge.__file__} declares "
            f"{policyforge.__version__}.",
            err=True,
        )
        ctx.exit(1)
    click.echo(f"policyforge {installed}")
    if installed != policyforge.__version__:
        click.echo(
            f"Note: the code running declares {policyforge.__version__} "
            f"({policyforge.__file__}), but the installed package's metadata says "
            f"{installed}. Reinstall to make them agree.",
            err=True,
        )
    ctx.exit()


@click.group(cls=_Group)
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Print the installed PolicyForge version and exit.",
)
def cli():
    """PolicyForge - cross-mapped compliance policy/procedure generation."""


# Registering the commands. Imported after `cli` exists, because each module
# decorates its commands with it.
from policyforge.cli import (  # noqa: E402
    content,
    crosswalk,
    documents,
    etl,
    library,
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
    "library",
    "llm",
    "load_config",
    "programme",
    "project",
    "zardoz",
]

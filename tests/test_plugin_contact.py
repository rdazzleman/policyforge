"""Where PolicyForge says the licensed-framework plugin is separate, it says
how to ask for it (#156).

The user's decision: the plugin is obtained by contacting the project, and
zardoz.io says so with a mailto to info@zardoz.io and the subject below. The
docs half of #156 is 80's (`test_docs_name_the_plugin.py`); this is the code's:
the refusal a user meets at the terminal, and the CLI help.

The address and subject are written here by hand, not read from
`plugins.PLUGIN_CONTACT`, so the test checks that constant rather than echoing
it. The populations are derived from the code: every command the CLI routes
through the plugin seam, and every command whose help names the plugin.
"""

from __future__ import annotations

import click
import pytest

from policyforge import plugins
from tests.test_plugins import _licensed_commands_in_the_cli

ADDRESS = "info@zardoz.io"
SUBJECT = "PolicyForge licensed-framework plugin"


@pytest.fixture
def no_plugin(monkeypatch):
    monkeypatch.setattr(plugins, "BUILTIN", [])
    monkeypatch.setattr(plugins, "_entry_points", lambda: [])
    plugins.reset()
    yield
    plugins.reset()


def test_the_premise_three_licensed_commands():
    """etl-hitrust, etl-govramp and etl-soc2-tsc, read from cli/etl.py."""
    assert _licensed_commands_in_the_cli() == {"etl-hitrust", "etl-govramp", "etl-soc2-tsc"}


@pytest.mark.parametrize("command", sorted(_licensed_commands_in_the_cli()))
def test_the_refusal_says_how_to_ask_for_the_plugin(no_plugin, command):
    with pytest.raises(plugins.PluginAbsent) as absent:
        plugins.require(command)
    said = str(absent.value)
    assert "distributed separately" in said, said
    assert ADDRESS in said and f'"{SUBJECT}"' in said, said
    # The contact comes before the next step, not buried after the README path.
    assert said.index(ADDRESS) < said.index("How this framework is brought in: "), said


def _commands(group, prefix=""):
    for name, command in group.commands.items():
        yield f"{prefix}{name}", command
        if isinstance(command, click.Group):
            yield from _commands(command, f"{prefix}{name} ")


def test_every_help_that_names_the_plugin_says_how_to_ask_for_it():
    """The population is every command whose help says the plugin is
    distributed separately, read from the live CLI, not from a list."""
    from policyforge.cli import cli

    naming = {
        name: " ".join((command.help or "").split())
        for name, command in _commands(cli)
        if "distributed separately" in " ".join((command.help or "").split())
    }
    assert {"etl-hitrust", "etl-govramp"} <= set(naming), "the premise: both help texts say it"
    for name, text in naming.items():
        assert ADDRESS in text and f'"{SUBJECT}"' in text, f"{name}: {text}"


def test_the_constant_is_the_sites_wording():
    """The one sentence both places use, checked against the hand-written
    address and subject."""
    assert ADDRESS in plugins.PLUGIN_CONTACT
    assert f'"{SUBJECT}"' in plugins.PLUGIN_CONTACT

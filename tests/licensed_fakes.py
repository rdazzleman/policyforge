"""A stand-in licensed-framework plugin, for public tests of the public half.

The licensed parsers left this tree for the separately distributed plugin
(#32, part 3). What these tests pin is public: `_run_licensed`'s write
guards, the licence envelope, the declared framework key. The real parse was
only how they reached it, so a fake plugin does that here: every licensed
command returns one invented control under its public framework name. The
plugin's own suite runs the same commands with the real parsers.
"""

from __future__ import annotations

import types
from pathlib import Path

from policyforge import plugins
from policyforge.ingest.schema import Control


class _Entry:
    name = "fake-licensed"
    value = "tests.licensed_fakes:PLUGIN"

    def __init__(self, plugin):
        self._plugin = plugin

    def load(self):
        return self._plugin


def _parser(command: str) -> plugins.Parser:
    framework = plugins.LICENSED_FRAMEWORK_NAMES[command]

    def parse(**_options):
        return plugins.ParseResult(
            controls=[Control("X.1", "An invented control", framework, "synthetic")],
            report="An invented report.",
            written="1 invented control",
            source="an invented export",
        )

    return plugins.Parser(command, framework, parse)


PLUGIN = types.SimpleNamespace(
    CONTRACT=plugins.CONTRACT_VERSION,
    PARSERS=[_parser(command) for command in plugins.LICENSED_FRAMEWORK_NAMES],
)


def install(monkeypatch) -> None:
    """Every licensed command, answered by the fake, for this test only."""
    monkeypatch.setattr(plugins, "BUILTIN", [])
    monkeypatch.setattr(plugins, "_entry_points", lambda: [_Entry(PLUGIN)])
    plugins.reset()


def build_workbook(path: Path, **_ignored) -> Path:
    """A placeholder export at `path`: the fake plugin never reads it."""
    path.write_bytes(b"placeholder: the fake licensed-framework plugin does not read this")
    return path

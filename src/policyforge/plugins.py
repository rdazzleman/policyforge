"""Where the licensed-framework parsers come from: a plugin, never this tree (#32).

The user's decision (2026-09-28): the parsers that read a licensed vendor's
file (HITRUST CSF, GovRAMP, the AICPA Trust Services Criteria) are not part
of the public, Apache-licensed tool. They live in a private package, which
registers them here through the `policyforge.licensed` entry-point group.
The public tool keeps the commands (`etl-hitrust`, `etl-govramp`,
`etl-soc2-tsc`), the write they end in, and `generate-parser`, which drafts a
parser from a customer's own sample.

**One resolver, so the call sites can't disagree about where a parser is,**
and **five refusals, never an empty answer that reads as "none"** (1d on
#32, comments 554 and 564):

- `PluginAbsent`: no installed plugin provides the command. Said plainly,
  pointing to `generate-parser`.
- `PluginNotFound`: a plugin is registered, but its declared module isn't
  there. Names the path tried.
- `PluginBroken`: the module is there and raised on import. Names the
  plugin and the error. Never read as "not installed", which would send the
  user to `generate-parser` for a broken install.
- `PluginAmbiguous`: two plugins provide one command or framework. Names
  both; neither is picked (the #15 rule).
- `PluginVersionMismatch`: the plugin targets another contract version.
  Names both versions, at load, rather than failing half-way through a
  user's export.

**Loaded lazily.** Nothing here runs at CLI import. A command asks for a
parser when it needs one, so a broken plugin can't break `check` or
`generate`, which never ask.

**The plugin parses; the public command writes.** A parser returns
`ParseResult` and never touches the output path. The GH#460 licensed-write
guard runs before it is called, and the #10 licence envelope is written
after it returns, so no plugin can write a licensed catalog past either
(1d on #32, comment 564).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

#: The plugin contract this public tool offers. A plugin declares the version
#: it was built against as `CONTRACT`; any other value is refused at load.
#: Raise it when `ParseResult` or a parser's calling convention changes.
CONTRACT_VERSION = 1

#: The entry-point group a licensed-framework plugin registers under. Each
#: entry point names a module (or object) exposing `CONTRACT` and `PARSERS`.
ENTRY_POINT_GROUP = "policyforge.licensed"

#: Framework names the licensed importers write into their rows. **A fixed
#: public list** (80's ruling on #32, comment 562): the names aren't
#: proprietary, and the unmarked-catalog warning in `llm/boundary.py` must
#: work for a user with no plugin, the user most likely to have a hand-made
#: licensed file lying around.
LICENSED_FRAMEWORK_NAMES = {
    "etl-hitrust": "HITRUST-CSF",
    "etl-govramp": "GovRAMP",
    "etl-soc2-tsc": "AICPA Trust Services Criteria",
}


class PluginError(Exception):
    """A licensed parser could not be provided. Each subclass says why."""


class PluginAbsent(PluginError):
    """No installed plugin provides the command."""


class PluginNotFound(PluginError):
    """A plugin is registered, but its module is not at the declared path."""


class PluginBroken(PluginError):
    """The plugin's module exists and raised on import."""


class PluginAmbiguous(PluginError):
    """Two plugins provide the same command or framework."""


class PluginVersionMismatch(PluginError):
    """The plugin was built for a different contract version."""


class ParseRefused(Exception):
    """A parser read the user's file and refuses to build a catalog from it.

    The plugin raises this, with its own reason, instead of its internal
    error types, so the public tool can print a refusal without knowing the
    plugin's classes. `suggest_generate_parser` is True when the file's
    LAYOUT is one the parser doesn't know, where drafting a parser from a
    sample can help, and False when the file was read and its content is the
    problem (#15), where it can't."""

    def __init__(self, message: str, *, suggest_generate_parser: bool = False):
        super().__init__(message)
        self.suggest_generate_parser = suggest_generate_parser


@dataclass
class ParseResult:
    """What a licensed parser hands back to the public command."""

    #: The catalog, as `ingest.schema.Control` objects.
    controls: list
    #: The parser's own report, printed as it is: counts, warnings, losses.
    report: str
    #: A line for after the write, e.g. "12 HITRUST control references".
    written: str = ""
    #: Manifest keys beyond the ones the public command writes itself (the
    #: name, `framework_id`, `licence: licensed`, `source`), e.g. SOC 2's
    #: crosswalk declarations. The public command owns the licence key.
    manifest: dict = field(default_factory=dict)
    #: The `framework_id` to declare. Empty: derived from the framework name,
    #: as `_declare_catalog` does.
    framework_id: str = ""
    #: Where the catalog came from, for the manifest's `source`.
    source: str = ""


@dataclass(frozen=True)
class Parser:
    """One licensed command a plugin provides."""

    command: str
    framework: str
    parse: Callable[..., ParseResult]
    #: The plugin it came from, for messages.
    origin: str = ""


def _entry_points() -> list:
    """The registered plugins, by `importlib.metadata`. A seam for tests."""
    from importlib.metadata import entry_points

    return list(entry_points(group=ENTRY_POINT_GROUP))


#: In-tree plugins, loaded exactly like entry points: (name,
#: "module:attribute") pairs. Empty: the licensed parsers left this tree
#: for the separately distributed licensed-framework plugin (#32, part 3),
#: which registers under the entry-point group. Kept as a seam for tests.
BUILTIN: list[tuple[str, str]] = []


@dataclass(frozen=True)
class _Source:
    name: str
    value: str

    def load(self):
        import importlib

        module_name, _, attribute = self.value.partition(":")
        module = importlib.import_module(module_name)
        return getattr(module, attribute) if attribute else module


def _sources() -> list:
    return [_Source(name, value) for name, value in BUILTIN] + _entry_points()


def _module_of(value: str) -> str:
    return value.partition(":")[0]


def _load(source) -> list[Parser]:
    """One plugin's parsers, or the refusal saying why not."""
    name, value = getattr(source, "name", "?"), getattr(source, "value", "?")
    try:
        plugin = source.load()
    except ModuleNotFoundError as exc:
        # Missing is "not found" only when it is the declared module (or a
        # parent of it) that is missing. A module the plugin itself imports
        # being missing is a broken plugin, not a wrong path.
        declared = _module_of(value)
        if exc.name and (declared == exc.name or declared.startswith(exc.name + ".")):
            raise PluginNotFound(
                f"The licensed-framework plugin {name!r} is registered at {value!r}, but no "
                f"module {declared!r} could be imported from there."
            ) from exc
        raise PluginBroken(
            f"The licensed-framework plugin {name!r} ({value}) failed to import: {exc}. "
            "It is installed but broken; reinstall it or fix its dependencies."
        ) from exc
    except Exception as exc:  # a plugin's import-time bug, whatever its type
        raise PluginBroken(
            f"The licensed-framework plugin {name!r} ({value}) failed to import: "
            f"{type(exc).__name__}: {exc}. It is installed but broken."
        ) from exc
    declared = getattr(plugin, "CONTRACT", None)
    if declared != CONTRACT_VERSION:
        raise PluginVersionMismatch(
            f"The licensed-framework plugin {name!r} ({value}) was built for plugin contract "
            f"{declared!r}; this PolicyForge offers contract {CONTRACT_VERSION}. Install a "
            "matching version of one or the other."
        )
    parsers = list(getattr(plugin, "PARSERS", ()) or ())
    return [Parser(p.command, p.framework, p.parse, origin=f"{name} ({value})") for p in parsers]


_cache: dict[str, Parser] | None = None


def parsers() -> dict[str, Parser]:
    """Every licensed command the installed plugins provide, by command name.

    Loaded once, on first use. Raises `PluginNotFound`, `PluginBroken`,
    `PluginVersionMismatch` or `PluginAmbiguous`. Absence is not an error
    here: the result is simply missing that command."""
    global _cache
    if _cache is None:
        found: dict[str, Parser] = {}
        frameworks: dict[str, Parser] = {}
        for source in _sources():
            for parser in _load(source):
                for key, table in ((parser.command, found), (parser.framework, frameworks)):
                    other = table.get(key)
                    if other is not None:
                        raise PluginAmbiguous(
                            f"{key!r} is provided twice: by {other.origin} and by "
                            f"{parser.origin}. Uninstall one; neither is chosen."
                        )
                    table[key] = parser
        _cache = found
    return _cache


def reset() -> None:
    """Forget what was loaded (tests, or a process that installs a plugin)."""
    global _cache
    _cache = None


#: `generate-parser --framework` values, per command. SOC 2 has none:
#: generate-parser drafts HITRUST and GovRAMP loaders only, so its absence
#: message must not point there.
GENERATE_PARSER_FRAMEWORK = {"etl-hitrust": "hitrust", "etl-govramp": "govramp"}


#: The directory whose README describes each licensed command's framework.
#: Every install ships that README (package data, and `policyforge init`
#: lays it out under data/frameworks/), so it's the one next step a user
#: without the plugin always has, and the only one for SOC 2, which
#: generate-parser doesn't draft (#101; 80's floor in 2068).
FRAMEWORK_README = {
    "etl-hitrust": "hitrust-csf",
    "etl-govramp": "govramp",
    "etl-soc2-tsc": "soc2-tsc",
}


def framework_readme(command: str) -> str:
    """Where the README for `command`'s framework is, as a path the user can
    open: the installed copy when one resolves, else its place in the
    PolicyForge repository. Empty for a command with no README."""
    directory = FRAMEWORK_README.get(command)
    if not directory:
        return ""
    try:
        from .scaffold import bundled_root

        readme = bundled_root().joinpath("frameworks", directory, "README.md")
        if readme.is_file():
            return str(readme)
    except (RuntimeError, OSError, ModuleNotFoundError):
        pass
    return f"data/frameworks/{directory}/README.md in the PolicyForge repository"


#: How to ask for the licensed-framework plugin, in the words zardoz.io uses
#: ("contact us about the plugin": a mailto to info@zardoz.io with this
#: subject). The user's decision on #156: the plugin is obtained by asking.
#: Said wherever PolicyForge says the plugin is distributed separately, and
#: `tests/test_plugin_contact.py` keeps the two together.
PLUGIN_CONTACT = (
    "To ask for it, contact info@zardoz.io with the subject "
    '"PolicyForge licensed-framework plugin".'
)


def require(command: str) -> Parser:
    """The parser for `command`, or a refusal naming why there isn't one.

    Absent, it says the plugin is distributed separately from the public
    tool and how to ask for it (`PLUGIN_CONTACT`, the user's decision on
    #156), and gives a next step that exists: `generate-parser` where it
    drafts this framework's loader, and always the framework's README
    (#101)."""
    parser = parsers().get(command)
    if parser is None:
        framework = GENERATE_PARSER_FRAMEWORK.get(command)
        draft = (
            " Without it, you can draft a parser for your own export with "
            f"`policyforge generate-parser --framework {shlex.quote(framework)} "
            "--sample <your file>`."
            if framework
            else ""
        )
        readme = framework_readme(command)
        read = f" How this framework is brought in: {readme}" if readme else ""
        raise PluginAbsent(
            f"`{command}` needs the licensed-framework plugin, which isn't installed. The "
            "parsers for licensed frameworks aren't part of the public PolicyForge: the "
            f"plugin is distributed separately. {PLUGIN_CONTACT} If your organisation "
            f"has it, install it into this environment.{draft}{read}"
        )
    return parser

from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config/config.yaml")

#: Names a config file to use instead of DEFAULT_CONFIG_PATH. Exists so a
#: second provider — a different model, or the same model on another
#: platform — can be run against this working tree without editing
#: config.yaml and then having to remember to put it back. Comparing two
#: models is the normal reason to want that, and a comparison that depends
#: on hand-editing one file is one crashed run away from silently grading
#: the wrong model.
CONFIG_PATH_ENV = "POLICYFORGE_CONFIG"


def resolve_config_path() -> Path:
    """The config file this process should read.

    Read at call time rather than captured at import, so that setting the
    variable inside a test (or between two runs in one session) takes
    effect rather than depending on import order.
    """
    override = os.environ.get(CONFIG_PATH_ENV)
    return Path(override) if override else DEFAULT_CONFIG_PATH


class ConfigError(ValueError):
    """The configuration can't be found or read (#254). The CLI turns it into
    one line, `Error: <message>`, and exit 2 (`cli._Group`).

    A `ValueError`, and each kind below also the exception it replaced, because
    callers already catch those: `frameworks.registry.config_or_defaults` warns
    once and falls back to the default search paths on `yaml.YAMLError` and
    `ValueError`, and the zardoz shell falls back on `FileNotFoundError` and
    `ValueError`. A new class outside those would escape both (measured on
    ag's first version: an unparsable or empty `config.yaml` raised out of
    `config_or_defaults` instead of falling back)."""


class ConfigNotFoundError(ConfigError, FileNotFoundError):
    """No config file at the path."""


class ConfigParseError(ConfigError, yaml.YAMLError):
    """The config file isn't valid YAML."""


class ConfigReadError(ConfigError, OSError):
    """The config path exists but can't be read: a directory, or no
    permission. (A file that isn't UTF-8 is a plain `ConfigError`, which is a
    `ValueError` like the `UnicodeDecodeError` it replaces.)"""


class ProviderSetupError(ConfigError, RuntimeError):
    """The configured model provider can't be built (#359): its key variable
    isn't set, or the optional extra it needs isn't installed. A `ConfigError`,
    so the CLI says it in one line with exit 2 instead of a traceback, and a
    `RuntimeError`, the exception these sites raised before, so a caller that
    catches that keeps catching it."""


class MissingCredentialError(ConfigError, RuntimeError):
    """A token a command needs is not in the environment variable the
    command was told to read it from (#176). A `ConfigError`, so the CLI says
    it in one line with exit 2, and a `RuntimeError`, the exception these
    sites raised before."""


class MissingLlmError(ConfigError, KeyError):
    """The config has no `llm:` block, or it names no provider (#176). Was a
    `KeyError: 'llm'` traceback from the first command that built a model."""

    def __str__(self) -> str:  # a KeyError would print its argument quoted
        return str(self.args[0]) if self.args else ""


def load_config(path: Path | None = None) -> dict:
    if path is None:
        path = resolve_config_path()
    if not path.exists():
        raise ConfigNotFoundError(
            f"{path} not found. Copy config/config.example.yaml to "
            f"{path} and fill in your model/provider choices."
        )
    # UTF-8 by name, not by the platform default. Every other text read in
    # this package says so; this one did not, and on Windows the default is
    # the console code page — so an organization or team name with a
    # character outside it, which is exactly what this file holds, either
    # mis-decoded silently or raised from inside yaml.
    #
    # Read whole first, so a file that can't be read or decoded is one line
    # too (b5 on PR 270: a cp1252-saved config, and a `config.yaml` that is a
    # directory, still ended in a traceback).
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path} isn't UTF-8 text ({exc.reason} at byte {exc.start}). Save it as UTF-8."
        ) from exc
    except OSError as exc:
        raise ConfigReadError(f"{path} can't be read: {exc.strerror or exc}.") from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # One line, as the CLI prints it (#254): PyYAML's own text spans
        # several (the problem, then "in <file>, line N, column M").
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem = getattr(exc, "problem", None) or " ".join(str(exc).split())
        raise ConfigParseError(f"Could not parse {path} as YAML: {problem}{where}") from exc

    # An empty or comment-only file parses to None, and a stray top-level
    # list parses to a list. Both then travel as far as the first
    # `config.get(...)` and fail as "'NoneType' object has no attribute
    # 'get'" — an error that never mentions the config file, from a
    # traceback deep inside a provider. Say it here, where the file is
    # still in hand.
    if loaded is None:
        raise ConfigError(
            f"{path} is empty. Copy config/config.example.yaml over it and "
            f"fill in your model/provider choices."
        )
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"{path} should contain a mapping of settings, but its top level "
            f"is {type(loaded).__name__}. Compare it with "
            f"config/config.example.yaml."
        )
    return loaded

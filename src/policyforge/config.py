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


def load_config(path: Path | None = None) -> dict:
    if path is None:
        path = resolve_config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config/config.example.yaml to "
            f"{path} and fill in your model/provider choices."
        )
    # UTF-8 by name, not by the platform default. Every other text read in
    # this package says so; this one did not, and on Windows the default is
    # the console code page — so an organization or team name with a
    # character outside it, which is exactly what this file holds, either
    # mis-decoded silently or raised from inside yaml.
    with path.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)

    # An empty or comment-only file parses to None, and a stray top-level
    # list parses to a list. Both then travel as far as the first
    # `config.get(...)` and fail as "'NoneType' object has no attribute
    # 'get'" — an error that never mentions the config file, from a
    # traceback deep inside a provider. Say it here, where the file is
    # still in hand.
    if loaded is None:
        raise ValueError(
            f"{path} is empty. Copy config/config.example.yaml over it and "
            f"fill in your model/provider choices."
        )
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{path} should contain a mapping of settings, but its top level "
            f"is {type(loaded).__name__}. Compare it with "
            f"config/config.example.yaml."
        )
    return loaded

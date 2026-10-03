"""The suite must not be able to reach a paid endpoint, by any route.

These tests exist because the obvious defence did not hold. `conftest.py`
stripped seven credential variables from the environment, which is correct
and was not enough: LiteLLM calls `load_dotenv()` at import, reads this
repository's own `.env` off disk, and writes the keys into `os.environ`
after the fixture has already run.

That was verified directly rather than argued: with `OPENROUTER_API_KEY`
absent from the environment, `import litellm` created it, populated from
`.env`, with a real key in it. Every test that ran afterwards held a live,
billable credential.

So the test below imports litellm on purpose. A test that carefully avoids
the import would pass while the hole stayed open.
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import _CREDENTIALS


def test_no_credential_is_visible_to_a_test():
    """The baseline: the fixture strips what was in the environment."""
    present = [name for name in _CREDENTIALS if os.environ.get(name)]
    assert present == [], f"live credential(s) reachable from a test: {present}"


def test_importing_litellm_does_not_resurrect_a_key_from_dotenv():
    """The failure the fixture alone did not cover.

    litellm's `load_dotenv()` at import is what put the key back. If this
    ever fails, the suite can bill the developer's account and reach a real
    model, and it will do so silently — a passing test that cost money.
    """
    pytest.importorskip("litellm")

    leaked = [name for name in _CREDENTIALS if os.environ.get(name)]
    assert leaked == [], (
        f"importing litellm restored credential(s) from .env: {leaked}. "
        "conftest disables dotenv's loader for exactly this reason; check "
        "that the patch still runs before anything imports litellm."
    )


def test_dotenv_loader_is_disarmed():
    """Directly, so a failure names the cause rather than a symptom.

    Passes in one of two ways, and never by skipping. With python-dotenv
    installed, its loader must be the conftest's no-op. Without it — it is
    not a dependency of this project, only of litellm and older mcp — nothing
    in the environment can load `.env` at all, and that is asserted instead.
    """
    import importlib.util

    if importlib.util.find_spec("dotenv") is None:
        return  # no loader exists to read .env: the commitment holds vacuously

    import dotenv

    assert dotenv.load_dotenv() is False
    assert dotenv.load_dotenv("some/other/.env") is False


def test_the_openrouter_key_is_covered():
    """Named explicitly: it is the one the project's own evals run on.

    A generic "strip everything plausible" list is easy to trim later by
    someone who does not know which entries were load-bearing. This one was
    missing, the measurements are run with `openrouter/...` model strings,
    and a live `.env` in this repo holds the key.
    """
    assert "OPENROUTER_API_KEY" in _CREDENTIALS

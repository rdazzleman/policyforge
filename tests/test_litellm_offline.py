"""Importing litellm under the suite reaches no network (#31).

LiteLLM fetches its model price table from raw.githubusercontent.com at
import, unless LITELLM_LOCAL_MODEL_COST_MAP is set. Measured: 1.100.1 (the
developer venv) and 1.103.0 (the CI lock) both do it. Locally the import
happened at collection, before `no_network` exists, so the suite made a
real request every run and nothing noticed. In CI, 87 tests failed on it.
tests/conftest.py now sets the variable at import. This checks the effect,
in a child process so its import is fresh, not the setting.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("litellm")

_CHILD = """
import socket
hosts = []
def spy(host, *a, **k):
    hosts.append(host)
    raise OSError(f"blocked {host}")
socket.getaddrinfo = spy
import litellm
print("LOOKUPS", hosts)
"""


def _import_litellm(env: dict) -> str:
    out = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    lines = [line for line in out.stdout.splitlines() if line.startswith("LOOKUPS")]
    return lines[0] if lines else out.stderr


def test_the_suite_sets_the_local_price_table():
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"


def test_importing_litellm_looks_up_no_host():
    assert _import_litellm(dict(os.environ)) == "LOOKUPS []"


def test_without_the_setting_the_import_does_reach_out():
    """The must-fail direction: the check above can fail, and the setting
    is what makes it pass."""
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    assert "raw.githubusercontent.com" in _import_litellm(env)

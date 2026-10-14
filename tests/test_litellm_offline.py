"""Importing litellm under the suite reaches no network (#31).

LiteLLM fetches its model price table from raw.githubusercontent.com at
import, unless LITELLM_LOCAL_MODEL_COST_MAP is set. Measured: 1.100.1 (the
developer venv) and 1.103.0 (the CI lock) both do it. Locally the import
happened at collection, before `no_network` exists, so the suite made a
real request every run and nothing noticed. In CI, 87 tests failed on it.
tests/conftest.py now sets the variable at import. This checks the effect,
in a child process so its import is fresh, not the setting.

**What decides each test is what the child reports, never how long it
took (#344).** The child prints each host it tries to resolve the moment it
tries, and `IMPORTED` when the import is done. The no-lookup test is
settled by any lookup, and the must-fail test by the price table's host
(not merely the first host, 1d on PR 350), so neither waits for the rest of
a heavy import. The import was what ran out a 120 s bound at a
loaded gate: unloaded, the lookup comes about 2 s in and the import ends
at about 4 s (b5, measured on 1.103.0). The bound left is a hang guard. If
it runs out first, the test says the child stopped reporting, which is a
fact about the child, not a finding about the network.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("litellm")

_CHILD = """
import socket
def spy(host, *a, **k):
    print("LOOKUP", host, flush=True)
    raise OSError(f"blocked {host}")
socket.getaddrinfo = spy
import litellm
print("IMPORTED", flush=True)
"""

#: A hang guard, not a performance bound: nothing here passes or fails on
#: how fast the import runs, only on what the child reported before it.
#: Running out says the child stopped reporting, and the tests say exactly that.
HANG_GUARD_S = 600


def _report(env: dict, *, until: str | None = None) -> tuple[list[str], bool, str]:
    """The hosts the child tried to resolve, whether its import finished,
    and, when it stopped short of both, why. It stops at `IMPORTED`, at the
    lookup of `until`, or at any lookup when `until` is None, or when the
    hang guard runs out."""
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    lines: queue.Queue = queue.Queue()
    errors: list[str] = []

    def read_stdout():
        for line in child.stdout:
            lines.put(line)
        lines.put(None)  # the child closed its output

    threading.Thread(target=read_stdout, daemon=True).start()
    stderr_reader = threading.Thread(target=lambda: errors.extend(child.stderr), daemon=True)
    stderr_reader.start()

    hosts: list[str] = []
    finished = timed_out = False
    deadline = time.monotonic() + HANG_GUARD_S
    try:
        while not finished and not (until in hosts if until else hosts):
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                timed_out = True  # no report in time: neither a lookup nor a clean import
                break
            if line is None:
                break  # the child ended without saying IMPORTED
            word, _, rest = line.strip().partition(" ")
            if word == "LOOKUP":
                hosts.append(rest)
            elif word == "IMPORTED":
                finished = True
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
    if finished or (until in hosts if until else hosts):
        return hosts, finished, ""
    stderr_reader.join(timeout=10)
    why = (
        f"the child stopped reporting: nothing more within the {HANG_GUARD_S} s hang guard"
        if timed_out
        else f"the child exited {child.returncode} before finishing the import"
    )
    return (
        hosts,
        finished,
        f"{why}, having resolved {hosts}. That says nothing more about the network. "
        f"Its stderr ends: {''.join(errors)[-1500:]!r}",
    )


def test_the_suite_sets_the_local_price_table():
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"


def test_importing_litellm_looks_up_no_host():
    hosts, finished, unreported = _report(dict(os.environ))

    assert hosts == [], f"the import tried to resolve {hosts}"
    assert finished, unreported


def test_without_the_setting_the_import_does_reach_out():
    """The must-fail direction: the check above can fail, and the setting
    is what makes it pass. It ends at the price table's lookup, whatever
    was resolved before it, and only a lookup the child reported counts (a
    URL in a warning on stderr is not one)."""
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    hosts, finished, unreported = _report(env, until="raw.githubusercontent.com")

    assert finished or "raw.githubusercontent.com" in hosts, unreported
    assert "raw.githubusercontent.com" in hosts, f"the import finished having resolved {hosts}"

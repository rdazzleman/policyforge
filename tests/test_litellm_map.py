"""The product pins LiteLLM's bundled map and applies its override table (#79).

80's ruling (1727): the product sets `LITELLM_LOCAL_MODEL_COST_MAP=True`
before LiteLLM is first imported, from one place (`llm/litellm_map.load`),
so what a user is told is what the suite tests and no run downloads a price
table. 1d's read list (1728) is the spec for these tests.

The wire tests run a child process with the variable REMOVED from its
environment (the suite's own conftest sets it, which would make them pass
vacuously), a spy on `socket.getaddrinfo`, and every API key dropped. Each
child also proves LiteLLM was actually imported, so "no lookup" can't come
from a path that never reached it.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from importlib import resources
from pathlib import Path

import pytest

from policyforge.llm import litellm_map

SRC = Path(__file__).resolve().parents[1] / "src" / "policyforge"

# ---- one place imports LiteLLM ------------------------------------------------


#: The functions that import a module named by a string.
_IMPORTERS = {"__import__", "import_module"}


def _litellm_imports(path: Path) -> list[int]:
    """Lines in `path` that import LiteLLM, in the spellings listed in
    `test_the_import_finder_sees_every_spelling`: `import`, `from`, a dotted
    submodule, and `__import__` or `importlib.import_module` called with a
    literal name, positionally or as `name=`, under any alias bound by
    `from importlib import import_module as X` (or `__import__ as X`).

    **What it cannot see:** a name built at run time (`mod = "litellm";
    import_module(mod)`, or `"lite" + "llm"`), and any code outside `src/`,
    such as a plugin `plugins.py` loads. The wire tests below cover those
    routes, and only the six they name (9b on #89).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    importers = set(_IMPORTERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            importers |= {a.asname for a in node.names if a.name in _IMPORTERS and a.asname}

    def names_litellm(value: ast.AST) -> bool:
        return isinstance(value, ast.Constant) and str(value.value).split(".")[0] == "litellm"

    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "litellm" or a.name.startswith("litellm.") for a in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] == "litellm":
                lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in importers and (
                any(names_litellm(a) for a in node.args)
                or any(k.arg == "name" and names_litellm(k.value) for k in node.keywords)
            ):
                lines.append(node.lineno)
    return lines


def test_only_litellm_map_imports_litellm():
    """Every module under src/ is read, not a list of the expected ones, so a
    new module that imports LiteLLM directly turns this red."""
    found = {
        str(p.relative_to(SRC)).replace("\\", "/"): lines
        for p in sorted(SRC.rglob("*.py"))
        if (lines := _litellm_imports(p))
    }
    assert found == {"llm/litellm_map.py": found.get("llm/litellm_map.py", [])}, found
    assert found.get("llm/litellm_map.py"), "the premise: the helper itself imports it"


def test_the_import_finder_sees_every_spelling(tmp_path):
    """The finder above, fed the spellings by hand, so its silence means something."""
    spellings = [
        "import litellm",
        "import litellm.utils",
        "from litellm import completion",
        "from litellm.utils import x",
        "import os, litellm",
        "__import__('litellm')",
        "importlib.import_module('litellm')",
        # 9b's on #89, which the first version missed:
        "from importlib import import_module as im; im('litellm')",
        "importlib.import_module(name='litellm')",
        "__import__(name='litellm')",
    ]
    for code in spellings:
        path = tmp_path / "m.py"
        path.write_text(f"def f():\n    {code}\n", encoding="utf-8")
        assert _litellm_imports(path) == [2], code
    path.write_text("from .litellm_map import load\nlitellm = load()\n", encoding="utf-8")
    assert _litellm_imports(path) == [], "the sanctioned route is not an import of litellm"
    # The stated limit, pinned: a name built at run time is NOT seen. If this
    # starts passing the other way, the docstring's limit is out of date.
    path.write_text(
        "def f():\n    mod = 'litellm'\n    importlib.import_module(mod)\n", encoding="utf-8"
    )
    assert _litellm_imports(path) == [], "a non-literal name is outside what the finder reads"


def test_the_python_dash_m_entry_imports_no_litellm():
    """1728 asked for `python -m policyforge` to be covered. Issue 427 added
    the entry (`__main__.py`), and the finder above reads it like any other
    module (it walks every file under `src`); pinned here so the coverage
    1728 asked for is a fact, not an inference."""
    entry = SRC / "__main__.py"
    assert entry.exists()
    assert _litellm_imports(entry) == []


# ---- on the wire -------------------------------------------------------------

_CHILD = """
import socket, sys
def spy(host, *a, **k):
    print("LOOKUP", host, flush=True)
    raise OSError(f"blocked {host}")
socket.getaddrinfo = spy
try:
    exec(sys.argv[1])
except SystemExit:
    pass
if "litellm" in sys.modules:
    print("IMPORTED", flush=True)
"""

#: Every route by which the product reaches LiteLLM, as a call a user's run
#: makes: the five `load()` sites in src/ (found by the finder above), and the
#: CLI entry point building the configured provider.
SITES = {
    "the provider's error class": (
        "from policyforge.llm.litellm_provider import _bad_request_error; _bad_request_error()"
    ),
    "building the provider": (
        "from policyforge.llm.litellm_provider import LiteLLMProvider; "
        "LiteLLMProvider(model='openrouter/anthropic/claude-sonnet-5')"
    ),
    "the schema capability": (
        "from policyforge.llm.litellm_provider import LiteLLMProvider; "
        "LiteLLMProvider(model='openrouter/anthropic/claude-sonnet-5', "
        "completion=lambda **k: None).supports_schema()"
    ),
    "a re-send's price": (
        "from policyforge.llm.escalation import _price; "
        "_price('openrouter/anthropic/claude-sonnet-5')"
    ),
    "the output ceiling": (
        "from policyforge.llm.ceiling import published_output_ceiling; "
        "published_output_ceiling('openrouter/anthropic/claude-sonnet-5')"
    ),
    "the CLI, llm-check": (
        "from click.testing import CliRunner; from policyforge.cli import cli; "
        "CliRunner().invoke(cli, ['llm-check'])"
    ),
}


#: A hang guard, not a performance bound: nothing here passes or fails on
#: how fast the import runs, only on what the child reported before it (#356).
#: Running out says the child stopped reporting, and the tests say exactly that.
HANG_GUARD_S = 600


def _child(
    code: str, env: dict, cwd: Path, *, site: str = "", until: str | None = None
) -> tuple[bool, list[str]]:
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
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

    if timed_out:
        stderr_reader.join(timeout=10)
        where = f"{site}: " if site else ""
        raise RuntimeError(
            f"{where}no report within the {HANG_GUARD_S} s hang guard. "
            f"That says nothing about the network. "
            f"Stderr ends: {''.join(errors)[-1500:]!r}"
        )

    if not finished and not hosts:
        stderr_reader.join(timeout=10)
        where = f"{site}: " if site else ""
        if child.returncode == 0:
            # It ran to its end, said nothing, and never loaded litellm: the
            # site no longer reaches it, which is the premise these tests
            # rest on (as the old `assert imported` said), not a crash.
            raise RuntimeError(f"{where}the premise: this site never reached LiteLLM")
        raise RuntimeError(
            f"{where}the child exited {child.returncode} before finishing the import. "
            f"Stderr ends: {''.join(errors)[-1500:]!r}"
        )

    return finished, hosts


@pytest.fixture
def user_env(tmp_path):
    """A user's environment: no pinning variable, no keys, a config that
    names a LiteLLM model, run from a directory with no .env."""
    pytest.importorskip("litellm")
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n  provider: litellm\n  model: openrouter/anthropic/claude-sonnet-5\n",
        encoding="utf-8",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "LITELLM_LOCAL_MODEL_COST_MAP" and not k.endswith("_API_KEY")
    }
    env["POLICYFORGE_CONFIG"] = str(config)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC.parent), env.get("PYTHONPATH", "")])
    return env, tmp_path


@pytest.mark.parametrize("site", sorted(SITES))
def test_no_route_downloads_the_price_table(site, user_env):
    env, cwd = user_env
    imported, lookups = _child(SITES[site], env, cwd, site=site)
    assert not lookups, f"{site}: reached the network: {lookups}"
    assert imported, f"the premise: {site} reached LiteLLM"


def test_a_user_false_does_not_turn_the_download_back_on(user_env):
    """Set unconditionally, as conftest sets it."""
    env, cwd = user_env
    env = {**env, "LITELLM_LOCAL_MODEL_COST_MAP": "False"}
    imported, lookups = _child(SITES["a re-send's price"], env, cwd, site="a re-send's price")
    assert not lookups, f"a re-send's price: reached the network: {lookups}"
    assert imported, "the premise: a re-send's price reached LiteLLM"


# ---- the harness itself (#356) -------------------------------------------------
#
# What the streaming reader must do, pinned apart from the six sites: decide on
# what the child reported, never on how long it took.


def test_a_lookup_settles_the_result_even_if_the_child_stalls_after_it(tmp_path, monkeypatch):
    """The issue's arm. The child looks a host up and then sleeps far past the
    hang guard. A reader that waited for the child to end would raise "no
    report"; this one has its answer at the lookup. Behaviour, not a stopwatch:
    the guard is the bound, and it is not reached."""
    monkeypatch.setattr(sys.modules[__name__], "HANG_GUARD_S", 20)
    code = chr(10).join(
        [
            "import socket, time",
            "try:",
            "    socket.getaddrinfo('stalled.example.com', 80)",
            "except OSError:",
            "    pass",
            "time.sleep(300)",
        ]
    )

    imported, lookups = _child(code, dict(os.environ), tmp_path, site="a stalling site")

    assert lookups == ["stalled.example.com"] and not imported


def test_a_child_that_never_reaches_litellm_says_the_premise_failed(tmp_path):
    with pytest.raises(RuntimeError, match="the premise: this site never reached LiteLLM"):
        _child("pass", dict(os.environ), tmp_path, site="a site that drifted")


def test_a_child_that_says_nothing_is_no_report_not_a_network_finding(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "HANG_GUARD_S", 5)

    with pytest.raises(RuntimeError, match=r"a mute site: no report within the 5 s hang guard"):
        _child("import time; time.sleep(300)", dict(os.environ), tmp_path, site="a mute site")


def test_the_harness_sees_the_download_when_nothing_pins_it(user_env):
    """The control: the same child and spy, importing LiteLLM the way the
    product used to, does look the table's host up. So the harness can see
    what the tests above say didn't happen."""
    env, cwd = user_env
    _imported, lookups = _child(
        "import litellm", env, cwd, site="control", until="raw.githubusercontent.com"
    )
    assert "raw.githubusercontent.com" in lookups, lookups


# ---- the override table ---------------------------------------------------------


def _bundled() -> dict:
    """LiteLLM's bundled map, read from the package's own file, not through
    `litellm.model_cost` (which `load()` has already amended)."""
    pytest.importorskip("litellm")
    path = resources.files("litellm") / "model_prices_and_context_window_backup.json"
    return json.loads(path.read_text(encoding="utf-8"))


REGISTERED = {"litellm_provider", "mode"}

ROOT = Path(__file__).resolve().parents[1]


def _versions() -> tuple[str, str]:
    """(the installed LiteLLM, the CI lock's pin), read from each's own
    source: the package metadata and `requirements/ci.txt`."""
    from importlib import metadata

    pytest.importorskip("litellm")
    lock = re.search(
        r"^litellm==([0-9][0-9.]*)", (ROOT / "requirements" / "ci.txt").read_text("utf-8"), re.M
    )
    assert lock, "requirements/ci.txt pins no litellm: the lock is the deletion signal's version"
    return metadata.version("litellm"), lock.group(1)


def _per_version(expected: dict):
    """The value written for the installed LiteLLM. Written by hand for the
    lock's 1.103.0 and for 1.104.0, what a fresh pip install got on
    2026-10-04 (#244); any other version skips, saying so."""
    installed, _ = _versions()
    if installed not in expected:
        pytest.skip(f"expectations are written for {sorted(expected)}, not LiteLLM {installed}")
    return expected[installed]


def test_every_override_says_where_it_came_from_and_when():
    today = dt.date.today()
    for key, entry in litellm_map.OVERRIDES.items():
        assert entry["source"] and entry["was"], key
        assert dt.date.fromisoformat(entry["as_of"]) <= today, key
        assert set(entry["litellm"]) - REGISTERED, f"{key} corrects nothing"


def test_every_override_still_corrects_the_bundled_map():
    """An entry the bundled map has caught up with fails here, so a redundant
    one is visible at the next LiteLLM bump (1728). **Asserted on the lock's
    version only** (80's ruling on #244): a Dependabot bump of the lock turns
    it red for the entry to delete, while a pip user's newer LiteLLM, where
    `load()` already lets the map's higher figure stand, doesn't."""
    installed, lock = _versions()
    if installed != lock:
        pytest.skip(
            f"the deletion signal is the lock's: LiteLLM {installed} installed, lock {lock}"
        )
    bundled = _bundled()
    for key, entry in litellm_map.OVERRIDES.items():
        corrects = litellm_map.effective(entry["litellm"], bundled.get(key))
        assert set(corrects) - REGISTERED, (
            f"{key}: the bundled map is at or above all of {entry['litellm']}"
        )


#: The price components the rule compares. A ceiling isn't a price: a
#: higher one lets a re-send ask for more, so it isn't held to "never below".
PRICES = ("input_cost_per_token", "output_cost_per_token")


def _below_bundled(overrides: dict, bundled: dict) -> list[tuple]:
    """Every (model, component, ours, bundled) where an override prices a
    component below the bundled map's figure for it."""
    below = []
    for key, entry in overrides.items():
        was = bundled.get(key, {})
        for field in PRICES:
            ours, theirs = entry["litellm"].get(field), was.get(field)
            if ours is not None and theirs is not None and ours < theirs:
                below.append((key, field, ours, theirs))
    return below


def test_no_override_prices_a_component_below_the_bundled_map():
    """80's rule on #110: per price component, the estimate is the higher of
    the bundled figure and the list read at review, so an override exists
    only where bundled is lower. An override BELOW the bundled figure would
    make the estimate understate, the harmful direction, and fails here."""
    litellm = litellm_map.load()
    bundled = _bundled()
    registered = {key: {"litellm": fields} for key, fields in litellm_map._registered.items()}
    assert not _below_bundled(registered, bundled), "a registered figure is below the map"
    # And what a user is priced at, after load(), is never below the map.
    priced = {key: {"litellm": litellm.model_cost[key]} for key in litellm_map.OVERRIDES}
    assert not _below_bundled(priced, bundled)


@pytest.mark.parametrize(
    ("ours", "expected"),
    [
        (
            {"input_cost_per_token": 1e-06, "output_cost_per_token": 12e-06},
            ["input_cost_per_token"],
        ),
        (
            {"input_cost_per_token": 3e-06, "output_cost_per_token": 9e-06},
            ["output_cost_per_token"],
        ),
        ({"input_cost_per_token": 3e-06, "output_cost_per_token": 12e-06}, []),
        ({"input_cost_per_token": 2e-06, "output_cost_per_token": 10e-06}, []),
        ({"output_cost_per_token": 12e-06}, []),
    ],
    ids=["input-below", "output-below", "both-above", "equal", "input-absent"],
)
def test_the_rule_check_sees_each_component_on_its_own(ours, expected):
    """1d's ARM D on #185: no real override's INPUT is below bundled today, so
    a check narrowed to output passed every real entry. Each component is
    pinned here against a synthetic bundled $2/M in, $10/M out. The expected
    names are written by hand, not taken from PRICES."""
    bundled = {"m": {"input_cost_per_token": 2e-06, "output_cost_per_token": 10e-06}}
    found = _below_bundled({"m": {"litellm": ours}}, bundled)
    assert [field for _, field, _, _ in found] == expected, found


def test_the_bundled_file_is_the_map_litellm_loads():
    """The premise of the two tests above: with the variable set, LiteLLM's
    table is that file (compared for a model no override touches)."""
    litellm = litellm_map.load()
    key = "openrouter/anthropic/claude-sonnet-5"
    assert key not in litellm_map.OVERRIDES
    assert litellm.model_cost[key] == _bundled()[key]


def test_opus_is_priced_and_ceilinged_as_openrouter_lists_it():
    """#78's opus figures under the pinned map plus the override. Written by
    hand from OpenRouter's model list (#79): $4/M in, $20/M out, 128,000 out.
    Under the bundled map alone it was unpriced, with a 64,000 fallback."""
    pytest.importorskip("litellm")
    from policyforge.llm import ceiling, escalation

    model = "openrouter/anthropic/claude-opus-5.5"
    ceiling._looked_up.cache_clear()
    try:
        assert ceiling.published_output_ceiling(model) == 128_000
    finally:
        ceiling._looked_up.cache_clear()
    price = escalation._price(model)
    assert price is not None and price[2] == model
    assert price[0] == pytest.approx(4e-06) and price[1] == pytest.approx(2e-05)


def test_a_price_names_its_basis():
    litellm_map.load()
    opus = litellm_map.basis("openrouter/anthropic/claude-opus-5.5")
    # 1.104.0's map caught up with opus, so its figures are the map's there.
    assert opus == _per_version(
        {
            "1.103.0": "PolicyForge's override table (OpenRouter model list, as of 2026-10-03)",
            "1.104.0": "LiteLLM 1.104.0's bundled table",
        }
    )
    said = litellm_map.basis("openrouter/anthropic/claude-sonnet-5")
    assert said.startswith("LiteLLM ") and said.endswith("'s bundled table")


def test_a_price_that_has_varied_says_so():
    """80 on #110: an entry with recorded readings is soft, and its basis says
    so. One without readings doesn't. On 1.104.0 the map is above kimi's list,
    so nothing is registered and the basis is the map's (#244)."""
    litellm_map.load()
    said = litellm_map.basis("openrouter/moonshotai/kimi-k3")
    assert said.endswith(
        _per_version(
            {
                "1.103.0": "as of 2026-10-03; this provider's price has varied)",
                "1.104.0": "LiteLLM 1.104.0's bundled table",
            }
        )
    ), said
    assert "varied" not in litellm_map.basis("openrouter/z-ai/glm-5.3-flash")


def test_kimi_is_priced_input_bundled_output_listed():
    """The rule applied (b5's read, 10-03 16:54 CDT, written here by hand): Kimi
    K3's input stays the bundled $1.70/M (the list's $0.639/M is lower), its
    output is the list's $13/M (above the bundled $8.50/M). DeepSeek has no
    override at all: bundled is at or above the list on every component."""
    pytest.importorskip("litellm")
    from policyforge.llm import escalation

    price = escalation._price("openrouter/moonshotai/kimi-k3")
    assert price is not None
    # On 1.104.0 the map rose to $3/$15, above the list on both, so it stands
    # (b5's measurement on #244, written here by hand).
    expected_in, expected_out = _per_version(
        {"1.103.0": (1.70e-06, 13e-06), "1.104.0": (3e-06, 15e-06)}
    )
    assert price[0] == pytest.approx(expected_in) and price[1] == pytest.approx(expected_out)
    for model in ("openrouter/deepseek/deepseek-v4-flash", "openrouter/deepseek/deepseek-v4-pro"):
        assert model not in litellm_map.OVERRIDES, model


# ---- the rule, applied at load against the map in use (80's ruling on #244) -----

#: 1.103.0's bundled entries for the overridden models, written by hand from
#: the wheel (b5 on #244), and the values registered against them before #244.
MAP_1_103 = {
    "openrouter/anthropic/claude-opus-5.5": None,
    "openrouter/z-ai/glm-5.3-flash": {
        "input_cost_per_token": 0.09e-06,
        "output_cost_per_token": 0.30e-06,
        "max_output_tokens": 131072,
    },
    "openrouter/moonshotai/kimi-k3": {
        "input_cost_per_token": 1.70e-06,
        "output_cost_per_token": 8.50e-06,
        "max_output_tokens": 943718,
    },
}
REGISTERED_BEFORE_244 = {
    "openrouter/anthropic/claude-opus-5.5": {
        "input_cost_per_token": 4e-06,
        "output_cost_per_token": 20e-06,
        "max_output_tokens": 128000,
        "litellm_provider": "openrouter",
        "mode": "chat",
    },
    "openrouter/z-ai/glm-5.3-flash": {
        "input_cost_per_token": 0.15e-06,
        "output_cost_per_token": 0.50e-06,
        "max_output_tokens": 943717,
    },
    "openrouter/moonshotai/kimi-k3": {"output_cost_per_token": 13e-06},
}


def test_against_1_103_s_map_the_rule_registers_exactly_what_it_did():
    """80's first test: no estimate on the lock's map moves."""
    for key, in_map in MAP_1_103.items():
        got = litellm_map.effective(litellm_map.OVERRIDES[key]["litellm"], in_map)
        assert got == pytest.approx(REGISTERED_BEFORE_244[key]), key


def test_a_map_figure_above_the_list_stands():
    """The glm and kimi case on 1.104.0: the map's output is above our list,
    so nothing is registered for it, and an estimate is never cut."""
    listed = {"input_cost_per_token": 0.15e-06, "output_cost_per_token": 0.50e-06}
    in_map = {"input_cost_per_token": 0.045e-06, "output_cost_per_token": 0.60e-06}
    assert litellm_map.effective(listed, in_map) == {"input_cost_per_token": 0.15e-06}


def test_a_list_figure_above_the_map_is_registered():
    listed = {"output_cost_per_token": 13e-06}
    assert litellm_map.effective(listed, {"output_cost_per_token": 8.5e-06}) == listed


def test_a_model_the_map_lacks_keeps_every_list_figure():
    listed = {"input_cost_per_token": 4e-06, "litellm_provider": "openrouter", "mode": "chat"}
    assert litellm_map.effective(listed, None) == listed
    assert litellm_map.effective(listed, {}) == listed


def test_routing_fields_are_kept_only_for_a_model_the_map_lacks():
    """`litellm_provider` and `mode` are not prices: a model the map already
    routes keeps the map's, so a caught-up entry (opus on 1.104.0) registers
    nothing and its basis is the map's."""
    listed = {"input_cost_per_token": 4e-06, "litellm_provider": "openrouter", "mode": "chat"}
    in_map = {"input_cost_per_token": 4e-06, "litellm_provider": "openrouter", "mode": "chat"}
    assert litellm_map.effective(listed, in_map) == {}


def test_the_rule_reads_the_map_litellm_loaded_not_a_file(monkeypatch):
    """80's condition on #244: the rule runs against `litellm.model_cost` after
    the import, the map the product prices with, whichever won. Here the map
    in memory says more for kimi's output than our list (as a newer or
    fetched map would): the map's figure must stand, and the basis must say
    so. A rule computed from a file, or frozen at review, registers $13."""
    litellm = litellm_map.load()
    key = "openrouter/moonshotai/kimi-k3"
    raised = {**litellm.model_cost[key], "output_cost_per_token": 21e-06}
    monkeypatch.setitem(litellm.model_cost, key, raised)
    monkeypatch.setattr(litellm_map, "_registered", None)
    litellm_map.load()
    assert key not in litellm_map._registered
    assert litellm.model_cost[key]["output_cost_per_token"] == pytest.approx(21e-06)
    assert litellm_map.basis(key).endswith("'s bundled table")


# ---- the source named per component (80's wording on #247) ---------------------

#: The component names `basis` uses, and the fields they stand for.
_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "output ceiling": "max_output_tokens",
}


def _named_sources(said: str, key: str) -> dict[str, str]:
    """{component: the source named for it}, read back from a basis string.
    Groups are split on "; " only OUTSIDE parentheses: the override's own
    "(…; this provider's price has varied)" holds one."""
    parts, depth, start = [], 0, 0
    for at, char in enumerate(said):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0 and said.startswith("; ", at):
            parts.append(said[start:at])
            start = at + 2
    parts.append(said[start:])
    if len(parts) == 1:  # uniform: every component the model has, from one source
        return dict.fromkeys(_present(key), said)
    named = {}
    for part in parts:
        names, source = part.split(": ", 1)
        for name in names.split(" and "):
            named[name] = source
    return named


def _present(key: str) -> list[str]:
    import litellm

    return [name for name, field in _FIELDS.items() if field in litellm.model_cost.get(key, {})]


@pytest.mark.parametrize(
    "key",
    [*litellm_map.OVERRIDES, "openrouter/anthropic/claude-sonnet-5"],
)
def test_each_named_source_holds_the_figure_it_is_named_for(key):
    """1d's test (80's rule): a user checking a figure against the source we
    name finds it there. Checked from the OTHER side: an override figure
    against `OVERRIDES`, a map figure against the package's own JSON file,
    each against what the product prices with, for whatever LiteLLM runs."""
    import litellm

    litellm_map.load()
    from importlib import metadata

    table = f"LiteLLM {metadata.version('litellm')}'s bundled table"
    named = _named_sources(litellm_map.basis(key), key)
    assert sorted(named) == sorted(_present(key)), named
    bundled = _bundled().get(key, {})
    for name, source in named.items():
        field, in_use = _FIELDS[name], litellm.model_cost[key][_FIELDS[name]]
        if source == table:
            assert bundled.get(field) == in_use, (key, name, source)
        else:
            assert source.startswith("PolicyForge's override table ("), (key, name, source)
            assert litellm_map.OVERRIDES[key]["litellm"].get(field) == in_use, (key, name)


OVERRIDE = "PolicyForge's override table (OpenRouter model list, as of 2026-10-03"


def test_a_mixed_model_names_each_source_and_a_uniform_one_names_one():
    """80's wording, written by hand per version (b5 on #247): kimi on 1.103.0
    and glm on 1.104.0 are mixed; the others are uniform."""
    litellm_map.load()
    expected = _per_version(
        {
            "1.103.0": {
                "openrouter/moonshotai/kimi-k3": (
                    "input and output ceiling: LiteLLM 1.103.0's bundled table; output: "
                    + OVERRIDE
                    + "; this provider's price has varied)"
                ),
                "openrouter/z-ai/glm-5.3-flash": OVERRIDE + ")",
                "openrouter/anthropic/claude-sonnet-5": "LiteLLM 1.103.0's bundled table",
            },
            "1.104.0": {
                "openrouter/z-ai/glm-5.3-flash": (
                    "input: "
                    + OVERRIDE
                    + "); output and output ceiling: LiteLLM 1.104.0's bundled table"
                ),
                "openrouter/moonshotai/kimi-k3": "LiteLLM 1.104.0's bundled table",
                "openrouter/anthropic/claude-sonnet-5": "LiteLLM 1.104.0's bundled table",
            },
        }
    )
    for key, said in expected.items():
        assert litellm_map.basis(key) == said, key


def test_the_resend_notice_and_the_ledger_carry_the_per_component_string():
    """80: the notice's frame is unchanged ("estimated at <model>'s price in
    <basis>"), and the ledger's `price_basis` stays a string with the same
    text, so there's no schema change."""
    import io

    from policyforge.llm import escalation

    litellm_map.load()
    key = _per_version(
        {"1.103.0": "openrouter/moonshotai/kimi-k3", "1.104.0": "openrouter/z-ai/glm-5.3-flash"}
    )
    said = litellm_map.basis(key)
    assert ": " in said, "a mixed model, or this test proves nothing about the per-component text"
    out = io.StringIO()
    token = escalation.begin()
    try:
        record = escalation.announce(
            model=key, first_max_tokens=1000, max_tokens=2000, input_tokens=100, out=out
        )
    finally:
        escalation.end(token, out=io.StringIO())
    assert f"estimated at {key}'s price in {said}." in out.getvalue(), out.getvalue()
    row = record.as_dict()
    assert isinstance(row["price_basis"], str) and row["price_basis"] == said


def test_the_string_follows_what_was_registered_whatever_litellm_runs(monkeypatch):
    """On the lock's data every declared component is registered and the map's
    group happens to come first, so "declared" and "registered", and any group
    order, agree there (b5's arms B and C survived on 1.103.0). A stand-in
    registration, glm's input only, separates them on any version: ours is
    what `load()` registered, and the group holding the first component comes
    first."""
    from importlib import metadata

    litellm_map.load()
    key = "openrouter/z-ai/glm-5.3-flash"
    monkeypatch.setattr(litellm_map, "_registered", {key: {"input_cost_per_token": 0.15e-06}})
    assert litellm_map.basis(key) == (
        "input: "
        + OVERRIDE
        + f"); output and output ceiling: LiteLLM {metadata.version('litellm')}'s bundled table"
    )


# ---- a reload after a reset still knows which figures are ours (#251) -----------


@pytest.mark.parametrize("key", sorted(litellm_map.OVERRIDES))
def test_a_reload_after_a_reset_names_the_same_sources(key, monkeypatch):
    """#251 (ag's test, per model): `register_model` writes our figures into
    the map in memory, so a second `load()` read our own figures as the
    map's, registered nothing, and `basis()` named LiteLLM's table for a
    figure that is ours. Measured before the fix: all three overridden models
    on 1.103.0."""
    litellm_map.load()
    before = (litellm_map.basis(key), dict(litellm_map._registered.get(key, {})))
    monkeypatch.setattr(litellm_map, "_registered", None)

    litellm_map.load()

    assert (litellm_map.basis(key), litellm_map._registered.get(key, {})) == before


def test_a_figure_someone_else_lowered_is_the_maps_and_our_list_beats_it_again(monkeypatch):
    """The other side of #244's rule: a figure changed after our write is the
    map's, whichever way it moved. Lowered below our list, our list is
    registered again and named."""
    litellm = litellm_map.load()
    key = "openrouter/moonshotai/kimi-k3"
    lowered = {**litellm.model_cost[key], "output_cost_per_token": 10e-06}
    monkeypatch.setitem(litellm.model_cost, key, lowered)
    monkeypatch.setattr(litellm_map, "_registered", None)

    litellm_map.load()

    assert litellm_map._registered[key]["output_cost_per_token"] == pytest.approx(13e-06)
    assert litellm.model_cost[key]["output_cost_per_token"] == pytest.approx(13e-06)
    assert "output: PolicyForge's override table" in litellm_map.basis(key)


@pytest.mark.parametrize("key", sorted(litellm_map.OVERRIDES))
def test_a_stand_in_map_read_in_between_doesnt_cost_the_real_one_its_record(key, monkeypatch):
    """9b on PR 290: a test that swaps in a stand-in `model_cost` made the
    record follow the stand-in, so when the real map came back a reload read
    our own figures in it as the map's. Every map keeps its own record.
    Per model: on 1.104.0 only glm registers, so kimi alone would not tell."""
    litellm = litellm_map.load()
    before = litellm_map.basis(key)
    real = litellm.model_cost

    monkeypatch.setattr(litellm, "model_cost", {})
    monkeypatch.setattr(litellm_map, "_registered", None)
    litellm_map.load()  # the stand-in, read and recorded

    monkeypatch.setattr(litellm, "model_cost", real)
    monkeypatch.setattr(litellm_map, "_registered", None)
    litellm_map.load()  # the real map again

    assert litellm_map.basis(key) == before

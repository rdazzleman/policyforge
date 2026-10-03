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
import subprocess
import sys
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


def test_there_is_no_python_dash_m_entry():
    """1728 asked for `python -m policyforge` to be covered. It doesn't exist
    (no `__main__.py`); if one is added, the finder above still covers what
    it imports. The CLI entry point is `policyforge.cli:cli` (pyproject)."""
    assert not (SRC / "__main__.py").exists()


# ---- on the wire -------------------------------------------------------------

_CHILD = """
import socket, sys
hosts = []
def spy(host, *a, **k):
    hosts.append(host)
    raise OSError(f"blocked {host}")
socket.getaddrinfo = spy
try:
    exec(sys.argv[1])
except SystemExit:
    pass
print("IMPORTED", "litellm" in sys.modules)
print("LOOKUPS", sorted(set(hosts)))
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


def _child(code: str, env: dict, cwd: Path) -> tuple[bool, str]:
    out = subprocess.run(
        [sys.executable, "-c", _CHILD, code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
        timeout=180,
    )
    lines = {line.split(" ", 1)[0]: line for line in out.stdout.splitlines()}
    assert "LOOKUPS" in lines, out.stderr[-2000:]
    return lines["IMPORTED"] == "IMPORTED True", lines["LOOKUPS"]


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
    imported, lookups = _child(SITES[site], env, cwd)
    assert imported, f"the premise: {site} reached LiteLLM"
    assert "raw.githubusercontent.com" not in lookups, f"{site}: {lookups}"


def test_a_user_false_does_not_turn_the_download_back_on(user_env):
    """Set unconditionally, as conftest sets it."""
    env, cwd = user_env
    env = {**env, "LITELLM_LOCAL_MODEL_COST_MAP": "False"}
    imported, lookups = _child(SITES["a re-send's price"], env, cwd)
    assert imported and "raw.githubusercontent.com" not in lookups, lookups


def test_the_harness_sees_the_download_when_nothing_pins_it(user_env):
    """The control: the same child and spy, importing LiteLLM the way the
    product used to, does look the table's host up. So the harness can see
    what the tests above say didn't happen."""
    env, cwd = user_env
    imported, lookups = _child("import litellm", env, cwd)
    assert imported and "raw.githubusercontent.com" in lookups, lookups


# ---- the override table ---------------------------------------------------------


def _bundled() -> dict:
    """LiteLLM's bundled map, read from the package's own file, not through
    `litellm.model_cost` (which `load()` has already amended)."""
    pytest.importorskip("litellm")
    path = resources.files("litellm") / "model_prices_and_context_window_backup.json"
    return json.loads(path.read_text(encoding="utf-8"))


REGISTERED = {"litellm_provider", "mode"}


def test_every_override_says_where_it_came_from_and_when():
    today = dt.date.today()
    for key, entry in litellm_map.OVERRIDES.items():
        assert entry["source"] and entry["was"], key
        assert dt.date.fromisoformat(entry["as_of"]) <= today, key
        assert set(entry["litellm"]) - REGISTERED, f"{key} corrects nothing"


def test_every_override_still_corrects_the_bundled_map():
    """An entry the bundled map has caught up with fails here, so a redundant
    one is visible at the next LiteLLM bump (1728)."""
    bundled = _bundled()
    for key, entry in litellm_map.OVERRIDES.items():
        was = bundled.get(key, {})
        corrected = {
            field
            for field, value in entry["litellm"].items()
            if field not in REGISTERED and was.get(field) != value
        }
        assert corrected, f"{key}: the bundled map already says all of {entry['litellm']}"


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
    assert litellm_map.basis("openrouter/anthropic/claude-opus-5.5") == (
        "PolicyForge's override table (OpenRouter model list, as of 2026-09-29)"
    )
    said = litellm_map.basis("openrouter/anthropic/claude-sonnet-5")
    assert said.startswith("LiteLLM ") and said.endswith("'s bundled table")

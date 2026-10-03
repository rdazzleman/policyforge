"""No test reads the developer's config (#26).

`tests/conftest.py::no_developer_config` points every test at a config file
that does not exist, unless the test is marked `own_config`. These tests go
red if that fixture is removed, narrowed to in-process only, or loses either
of its teardown checks. Measured on the fix: with the fixture removed, the
first two fail on any machine, whether or not the person running them keeps a
config. So they don't depend on the developer having one to show a leak.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import _CONFIG_CACHES, _PLUGIN_CACHES, _ROOT, config_leak

from policyforge.config import load_config, resolve_config_path

#: Read at COLLECTION, before any fixture: what a module-level call sees
#: (1d on #29, where one in test_level_scoping.py keyed through the
#: developer's config).
_CONFIG_AT_COLLECTION = os.environ.get("POLICYFORGE_CONFIG")

# Fill every config-derived cache at collection, on purpose, so the test
# below is red whatever order tests run in, if they aren't emptied per test.
from policyforge.content.tags import known_framework_names  # noqa: E402
from policyforge.mapping.crosswalk import normalize_framework  # noqa: E402
from policyforge.topics.anchoring import _declared_rules  # noqa: E402

normalize_framework("NIST SP 800-53 Rev 5")
known_framework_names()
_declared_rules()


def test_collection_already_saw_no_config():
    """The variable is set before collection, not only per test, so
    module-level code reads the clean state too."""
    assert _CONFIG_AT_COLLECTION, "POLICYFORGE_CONFIG was unset at collection"
    assert not Path(_CONFIG_AT_COLLECTION).exists()
    assert Path(_CONFIG_AT_COLLECTION).name == "no-config-for-this-session.yaml"


#: Process caches in `src/` filled from the BUNDLED catalogs, which config
#: can't change. Written by hand from reading each fill, so it's the other
#: side of the check, not the conftest's list.
_BUNDLED_ONLY = {
    ("policyforge.content.deontic", "_SUBCATEGORIES"),
    ("policyforge.content.deontic", "_PLAYBOOK_IDS"),
    ("policyforge.content.deontic", "_PLAYBOOK_ACTIONS"),
    # Not a catalog, but config can't change it either: whether
    # litellm_map.OVERRIDES, a code constant, is registered yet (#79).
    ("policyforge.llm.litellm_map", "_registered"),
}


def _swept_caches() -> set[tuple[str, str]]:
    """Every module global in `src/` that some function assigns through a
    `global` statement: the population, derived from the code rather than
    from anybody's list. It doesn't see a module-level container mutated in
    place, which needs no `global`; see `conftest._CONFIG_CACHES` for that
    bound."""
    import re

    src = _ROOT / "src"
    found = set()
    for path in (src / "policyforge").rglob("*.py"):
        module = ".".join(path.relative_to(src).with_suffix("").parts)
        for names in re.findall(r"^\s+global\s+([\w\s,]+)$", path.read_text("utf-8"), re.M):
            found |= {(module, n.strip()) for n in names.split(",")}
    return found


def test_every_process_cache_in_src_is_classified():
    """A new cache has to be classified: config-derived or plugin-derived
    (#32), so the conftest empties it per test, or bundled-only, so it needn't
    be. The total is what can't shrink: dropping one from the conftest's
    lists fails here."""
    swept = _swept_caches()
    assert len(swept) >= 6, swept  # the sweep itself found something
    assert swept == set(_CONFIG_CACHES) | set(_PLUGIN_CACHES) | _BUNDLED_ONLY


def test_every_config_derived_cache_starts_each_test_empty():
    """Each was filled at this module's collection, above. The set comes
    from the sweep, not from the conftest's list."""
    import importlib

    for module, name in sorted(_swept_caches() - _BUNDLED_ONLY):
        assert getattr(importlib.import_module(module), name) is None, f"{module}.{name}"


def test_a_test_resolves_to_no_config_at_all(tmp_path):
    path = resolve_config_path()
    assert path.parent == tmp_path, path
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        load_config()


def test_a_child_process_inherits_the_isolation(tmp_path):
    """An environment variable, not a patch, so `policyforge` run as a
    subprocess by a test is isolated too."""
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('POLICYFORGE_CONFIG', ''))"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert Path(child.stdout.strip()).parent == tmp_path


@pytest.mark.own_config
def test_an_own_config_test_reads_the_config_it_builds(tmp_path, monkeypatch):
    """The must-stay direction: opting in reads the test's own file."""
    assert "POLICYFORGE_CONFIG" not in os.environ
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("org:\n  name: Initech\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_config()["org"]["name"] == "Initech"


def test_writing_a_config_without_opting_in_is_named(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("org: {}\n", encoding="utf-8")
    assert "Mark the test @pytest.mark.own_config" in config_leak(
        tmp_path, own=False, override=str(tmp_path / "x.yaml")
    )
    assert config_leak(tmp_path / "config", own=False, override=None) == ""


def test_the_fixture_fails_a_test_that_hides_its_own_config(tmp_path):
    """The wiring, not just `config_leak`. An inner pytest session loads
    this suite's conftest as a plugin and runs a test that writes its own
    config without opting in. That test must fail with the guard's message.
    Unit tests of `config_leak` alone stay green if the fixture stops
    calling it."""
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "def test_writes_its_own(tmp_path, monkeypatch):\n"
        "    (tmp_path / 'config').mkdir()\n"
        "    (tmp_path / 'config' / 'config.yaml').write_text('org: {}\\n')\n"
        "    monkeypatch.chdir(tmp_path)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(_ROOT / "tests"), str(_ROOT / "src")])}
    inner = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-q",
            str(probe),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
        env=env,
    )
    assert inner.returncode == 1, inner.stdout + inner.stderr
    assert "Mark the test @pytest.mark.own_config" in inner.stdout, inner.stdout


def test_opting_in_while_standing_in_the_checkout_is_named(tmp_path):
    assert "would read the developer's config" in config_leak(_ROOT, own=True, override=None)
    assert config_leak(_ROOT, own=True, override=str(tmp_path / "mine.yaml")) == ""
    assert config_leak(tmp_path, own=True, override=None) == ""

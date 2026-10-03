"""A catalog keys by the `framework_id:` its `framework.yaml` declares (#295, PR 1 of 2).

`normalize_framework` keyed a framework by the first word of its prose name
unless an alias needle matched first, and every sibling of a pinned name fell
through to the bare word (#176 recorded seven). 80's ruling: a declared key in
`framework.yaml` (`framework_id:`, not `key:`, which gitleaks reads as a
secret); the declaration wins; a tree-derived test that each shipped
catalog's declared key equals today's key, so this change moves no key; and a
disagreement for a shipped catalog is a test failure.

**Read from the directories, not registered at load** (80's condition): a
name keyed before any catalog is loaded -- a citation parsed first -- must
still get the declared key.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from policyforge.frameworks.registry import (
    FrameworkKeyWarning,
    declared_keys,
    key_collisions,
    load_framework,
)
from policyforge.mapping import crosswalk
from policyforge.mapping.crosswalk import normalize_framework, prose_framework_key

CATALOGS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


@pytest.fixture
def fresh_keys():
    """Read the declarations again before and after, so no test sees another's."""
    crosswalk.reset_declared_keys()
    yield
    crosswalk.reset_declared_keys()


def _names(directory: Path) -> set[str]:
    framework = load_framework(directory)
    rows = json.loads((directory / "controls.json").read_text(encoding="utf-8"))
    return {framework.name} | {r["framework"] for r in rows}


# -- the shipped tree: declared, and equal to today's key -------------------------------


def _shipped() -> list[Path]:
    return [d for d in sorted(CATALOGS.iterdir()) if (d / "controls.json").exists()]


def test_every_shipped_catalog_declares_a_key():
    assert len(_shipped()) >= 10
    missing = [d.name for d in _shipped() if not load_framework(d).key]
    assert not missing, f"no `framework_id:` in framework.yaml: {missing}"


@pytest.mark.parametrize("directory", _shipped(), ids=lambda d: d.name)
def test_the_declared_key_equals_todays_key_for_every_name(directory):
    """**No key moves.** Today's key is the prose one -- alias, else first
    word -- and it must equal the declaration for EVERY name the catalog goes
    by, the manifest's and the one its rows carry. Citations and overlays
    written before this change key through the prose path; a disagreement
    here would re-key them silently."""
    key = load_framework(directory).key
    for name in _names(directory):
        assert prose_framework_key(name) == key, (directory.name, name)


def test_the_shipped_tree_declares_without_a_single_disagreement():
    with warnings.catch_warnings():
        warnings.simplefilter("error", FrameworkKeyWarning)
        keys = declared_keys(roots=[CATALOGS])
    assert keys["nist 800-53"] == "nist-800-53"
    assert keys["hipaa security rule"] == "hipaa"
    assert keys["nist sp 800-171 rev 3"] == "nist-800-171"


# -- the declaration wins, with nothing loaded -----------------------------------------------


def _catalog(root: Path, directory: str, *, name: str, key: str, framework: str | None = None):
    path = root / directory
    path.mkdir(parents=True)
    (path / "framework.yaml").write_text(f"name: {name}\nframework_id: {key}\n", encoding="utf-8")
    row = {
        "control_id": "X-1",
        "title": "t",
        "framework": framework or name,
        "framework_version": "1",
    }
    (path / "controls.json").write_text(json.dumps([row]), encoding="utf-8")


def test_a_byoc_declared_name_keys_by_its_declaration_with_nothing_loaded(
    tmp_path, monkeypatch, fresh_keys
):
    """80's condition, as its test: no `load_controls`, no `load_catalogs`
    -- only the directory on disk, found through the default search path."""
    _catalog(
        tmp_path / "frameworks", "acme-baseline", name="Acme Security Baseline", key="acme-baseline"
    )
    monkeypatch.chdir(tmp_path)
    assert prose_framework_key("Acme Security Baseline") == "acme"  # the premise
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FrameworkKeyWarning)
        assert normalize_framework("Acme Security Baseline") == "acme-baseline"
        # Case and spacing as a hand-written citation might carry them.
        assert normalize_framework("  acme   SECURITY baseline ") == "acme-baseline"
        # Only the declared name: a longer one is someone else's.
        assert normalize_framework("Acme Security Baseline Extended") == "acme"


def _silently_keys(name: str) -> str:
    """Key `name` and fail on any FrameworkKeyWarning: keying is silent about
    prose collisions on every command (80's ruling on #347)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", FrameworkKeyWarning)
        return normalize_framework(name)


def test_a_sibling_of_a_pinned_name_no_longer_falls_through(tmp_path, monkeypatch, fresh_keys):
    """The class #295 is about: `NIST Privacy Framework` has no alias, so its
    prose key is bare `nist`. Declared, it keys to itself, silently, and
    `key_collisions` states the fact: `nist` is the first word of a shipped
    NIST catalog, the bucket every unpinned NIST citation was filed under
    (80's rulings on #347)."""
    _catalog(
        tmp_path / "frameworks", "nist-privacy", name="NIST Privacy Framework", key="nist-privacy"
    )
    monkeypatch.chdir(tmp_path)
    assert _silently_keys("NIST Privacy Framework") == "nist-privacy"
    (fact,) = key_collisions()
    assert "'nist-privacy'" in fact and "would key to 'nist'" in fact
    assert "the first word of shipped catalog 'NIST" in fact


def test_the_shipped_buckets_are_derived_from_the_shipped_catalogs():
    """Rule (3) of 80's known keys: the first word of every name a shipped
    catalog goes by, read from the tree -- so a catalog added later adds
    its bucket without anyone typing it."""
    from policyforge.frameworks.registry import _shipped_buckets

    expected = {name.lower().split()[0] for d in _shipped() for name in _names(d)}
    assert set(_shipped_buckets()) == expected
    assert {"nist", "hipaa"} <= expected and "acme" not in expected


def test_the_fragments_own_example_declares_silently(tmp_path, monkeypatch, fresh_keys):
    """80's PR 2 ruling: a user following the fragment exactly gets no
    warning and no fact. `acme` is nobody's key, so nothing can mislead."""
    _catalog(
        tmp_path / "frameworks", "acme-baseline", name="Acme Security Baseline", key="acme-baseline"
    )
    monkeypatch.chdir(tmp_path)
    assert _silently_keys("Acme Security Baseline") == "acme-baseline"
    assert key_collisions() == []


def test_a_name_that_prose_files_under_another_frameworks_key_is_stated(
    tmp_path, monkeypatch, fresh_keys
):
    """The real hazard (80): `NIST SP 800-53 Privacy Overlay` keys by prose to
    `nist-800-53` -- 800-53's key -- so citations written before its
    declaration were filed under the wrong catalog. Stated, with both keys
    and the catalog that declares the other one."""
    _catalog(
        tmp_path / "frameworks",
        "privacy-overlay",
        name="NIST SP 800-53 Privacy Overlay",
        key="nist-800-53-privacy-overlay",
    )
    assert prose_framework_key("NIST SP 800-53 Privacy Overlay") == "nist-800-53"  # the premise
    monkeypatch.chdir(tmp_path)
    assert _silently_keys("NIST SP 800-53 Privacy Overlay") == "nist-800-53-privacy-overlay"
    (fact,) = key_collisions()
    assert "'nist-800-53-privacy-overlay'" in fact and "would key to 'nist-800-53'" in fact
    assert "nist-800-53-r5 declares" in fact


def test_frameworks_shows_the_fact_and_a_keying_command_does_not(tmp_path, monkeypatch, fresh_keys):
    """80's ruling on #347: shown where declarations are inspected, never on
    every command that loads catalogs. `map` stands for the latter because
    it keys every control's framework, including this catalog's; `check`
    does not call `normalize_framework` at all, so it could not show one."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    _catalog(
        tmp_path / "frameworks", "nist-privacy", name="NIST Privacy Framework", key="nist-privacy"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    listed = CliRunner().invoke(cli_mod.cli, ["frameworks"])
    assert "would key to 'nist'" in listed.output, listed.output

    with warnings.catch_warnings():
        warnings.simplefilter("error", FrameworkKeyWarning)
        mapped = CliRunner().invoke(
            cli_mod.cli,
            [
                "map",
                "--controls",
                str(CATALOGS / "nist-800-53-r5" / "controls.json"),
                "--controls",
                str(tmp_path / "frameworks" / "nist-privacy" / "controls.json"),
                "--out",
                str(tmp_path / "crosswalk.json"),
            ],
        )
    assert mapped.exit_code == 0, (mapped.output, mapped.exception)
    assert "would key to" not in mapped.output


def test_two_catalogs_declaring_one_name_differently_are_named_and_the_first_wins(
    tmp_path, fresh_keys
):
    first, second = tmp_path / "a", tmp_path / "b"
    _catalog(first, "one", name="Shared Name", key="shared-one")
    _catalog(second, "two", name="Shared Name", key="shared-two")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", FrameworkKeyWarning)
        keys = declared_keys(roots=[first, second])
    messages = [str(w.message) for w in caught if issubclass(w.category, FrameworkKeyWarning)]
    assert keys["shared name"] == "shared-one"
    # One warning, the conflict. "Shared Name" prose-keys to `shared`, which is
    # nobody's key, so its difference from 'shared-one' is silent (PR 2).
    assert any("declared as 'shared-one'" in m and "as 'shared-two'" in m for m in messages)
    assert len(messages) == 1, messages


def test_the_bundled_catalogs_declare_from_any_working_directory(tmp_path, monkeypatch):
    """An installed wheel has no `data/frameworks` under the working
    directory, so the shipped declarations must come from the bundled root,
    not from the search path that happens to find them in a checkout."""
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", FrameworkKeyWarning)
        keys = declared_keys()
    assert keys.get("nist 800-53") == "nist-800-53"
    assert keys.get("nist ai rmf playbook") == "nist-ai-rmf-playbook"


@pytest.mark.own_config
def test_a_malformed_config_is_named_once_and_does_not_break_keying(
    tmp_path, monkeypatch, fresh_keys
):
    """1d on #344: `normalize_framework` read config, and `org: [unclosed`
    turned `policyforge map` -- which never reads config itself -- from exit
    0 into a ParserError traceback. Now one warning names the file, and the
    default search paths still declare (a BYOC catalog under `frameworks/`
    keeps its key)."""
    from click.testing import CliRunner

    from policyforge.cli import cli

    _catalog(
        tmp_path / "frameworks", "acme-baseline", name="Acme Security Baseline", key="acme-baseline"
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("org: [unclosed\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", FrameworkKeyWarning)
        result = CliRunner().invoke(
            cli,
            [
                "map",
                "--controls",
                str(CATALOGS / "nist-800-53-r5" / "controls.json"),
                "--controls",
                str(CATALOGS / "hipaa-security-rule" / "controls.json"),
                "--out",
                str(tmp_path / "crosswalk.json"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert normalize_framework("Acme Security Baseline") == "acme-baseline"
    named = [str(w.message) for w in caught if "could not be read" in str(w.message)]
    assert len(named) == 1 and "config.yaml" in named[0], named


def test_a_failed_build_is_not_cached_as_no_declarations(tmp_path, monkeypatch, fresh_keys):
    """The second half of 1d's finding: the cache was set to `{}` before the
    build, so after one failure every later lookup keyed by prose, silently.
    A failure now propagates, and the next lookup builds again."""
    _catalog(
        tmp_path / "frameworks", "acme-baseline", name="Acme Security Baseline", key="acme-baseline"
    )
    monkeypatch.chdir(tmp_path)
    real = crosswalk.declared_keys_from_config
    calls = []

    def fails_once():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk went away")
        return real()

    monkeypatch.setattr(crosswalk, "declared_keys_from_config", fails_once)
    with pytest.raises(OSError, match="disk went away"):
        normalize_framework("Acme Security Baseline")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FrameworkKeyWarning)
        assert normalize_framework("Acme Security Baseline") == "acme-baseline"
    assert len(calls) == 2


def test_a_catalog_without_a_key_declares_nothing(tmp_path):
    path = tmp_path / "plain"
    path.mkdir()
    (path / "framework.yaml").write_text("name: Plain Catalog\n", encoding="utf-8")
    assert declared_keys(roots=[tmp_path]) == {}

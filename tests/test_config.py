"""Loading config.yaml, including the shapes it should refuse.

Seventeen lines and no tests, which understates what they decide. Every
command starts by calling this, so a bad return value here does not fail
here — it fails several frames deeper, inside a provider, as an
AttributeError that never mentions the config file.
"""

from __future__ import annotations

import pytest

from policyforge.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_config_path,
)


def _config(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_populated_config_loads(tmp_path):
    path = _config(tmp_path, "provider: anthropic\nmodel: claude-opus-5\n")

    assert load_config(path) == {"provider": "anthropic", "model": "claude-opus-5"}


def test_a_missing_file_says_how_to_make_one(tmp_path):
    with pytest.raises(FileNotFoundError) as caught:
        load_config(tmp_path / "nope.yaml")

    assert "config.example.yaml" in str(caught.value)


@pytest.mark.parametrize("text", ["", "   \n", "# nothing configured yet\n"])
def test_an_empty_config_is_refused_by_name(tmp_path, text):
    """`yaml.safe_load` returns None for these, which every caller then
    dereferences. The old failure was "'NoneType' object has no attribute
    'get'" from inside a provider, with no mention of the file."""
    with pytest.raises(ValueError) as caught:
        load_config(_config(tmp_path, text))

    assert "config.yaml" in str(caught.value)
    assert "empty" in str(caught.value)


@pytest.mark.parametrize("text", ["- one\n- two\n", "just a string\n", "42\n"])
def test_a_config_that_is_not_a_mapping_is_refused_by_name(tmp_path, text):
    """A top-level list is the shape a real config drifted into once
    already, for the `frameworks:` key."""
    with pytest.raises(ValueError) as caught:
        load_config(_config(tmp_path, text))

    assert "mapping" in str(caught.value)


def test_the_error_names_the_type_that_was_found(tmp_path):
    """So the reader can see what they wrote without opening the file."""
    with pytest.raises(ValueError) as caught:
        load_config(_config(tmp_path, "- one\n"))

    assert "list" in str(caught.value)


def test_without_the_env_var_the_default_path_is_used(monkeypatch):
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)

    assert resolve_config_path() == DEFAULT_CONFIG_PATH


def test_the_env_var_selects_a_different_config(monkeypatch, tmp_path):
    """Comparing two models is the reason this exists.

    Editing config.yaml back and forth works right up until a run dies
    half way and leaves the other provider's config in place, after which
    the next run grades a model nobody chose and says nothing about it.
    """
    path = _config(tmp_path, "llm:\n  provider: vertex\n  model: claude-opus-5\n")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(path))

    assert resolve_config_path() == path
    assert load_config()["llm"]["provider"] == "vertex"


def test_the_env_var_is_read_per_call_not_at_import(monkeypatch, tmp_path):
    """Set between two runs in one process, it must still take effect."""
    first = _config(tmp_path, "llm:\n  model: one\n")
    second = tmp_path / "other.yaml"
    second.write_text("llm:\n  model: two\n", encoding="utf-8")

    monkeypatch.setenv(CONFIG_PATH_ENV, str(first))
    assert load_config()["llm"]["model"] == "one"

    monkeypatch.setenv(CONFIG_PATH_ENV, str(second))
    assert load_config()["llm"]["model"] == "two"


def test_an_explicit_path_still_wins_over_the_env_var(monkeypatch, tmp_path):
    """Every existing caller passes a path; none of them should start
    silently following an env var somebody exported for a different run."""
    explicit = _config(tmp_path, "llm:\n  model: explicit\n")
    ignored = tmp_path / "ignored.yaml"
    ignored.write_text("llm:\n  model: ignored\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(ignored))

    assert load_config(explicit)["llm"]["model"] == "explicit"


def test_names_outside_the_console_code_page_survive_loading(tmp_path):
    """The config file is where an organization's own names live.

    It was the one text read in the package that named no encoding, so on
    Windows it used the console code page: a name outside it raised from
    inside yaml, and one inside it but not ASCII could decode to something
    else without a word. Two scripts, so the test fails either way.
    """
    path = _config(tmp_path, 'org:\n  name: "Sécurité Santé"\n  team: "医療情報部"\n')

    loaded = load_config(path)

    assert loaded["org"] == {"name": "Sécurité Santé", "team": "医療情報部"}

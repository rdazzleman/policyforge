"""The default provider.

`test_llm.py` covers the API shim, Vertex and Bedrock; the provider almost
everybody actually runs had nothing. Driven through an injected fake
client, so none of this touches the network.
"""

from __future__ import annotations

import sys
import types

import pytest

from policyforge.llm.anthropic_provider import AnthropicProvider
from tests.test_llm import FakeAnthropicSdkClient


@pytest.fixture
def anthropic_module(monkeypatch):
    """Stand in for the `anthropic` package, which is imported lazily."""
    created = {}

    class _Anthropic:
        def __init__(self, *, api_key):
            created["api_key"] = api_key
            self.messages = FakeAnthropicSdkClient().messages

    module = types.ModuleType("anthropic")
    module.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return created


def test_a_missing_key_names_the_variable_to_set(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as caught:
        AnthropicProvider(model="claude-opus-5")

    assert "ANTHROPIC_API_KEY" in str(caught.value)


def test_the_key_comes_from_the_environment_not_from_config(monkeypatch, anthropic_module):
    """A literal key in config.yaml gets committed. The variable name is
    configurable; the value is never read from the file."""
    monkeypatch.setenv("MY_OWN_KEY_VAR", "sk-ant-secret")

    AnthropicProvider(model="claude-opus-5", api_key_env="MY_OWN_KEY_VAR")

    assert anthropic_module["api_key"] == "sk-ant-secret"


def test_a_missing_custom_variable_is_reported_by_its_own_name(monkeypatch):
    monkeypatch.delenv("SOME_OTHER_VAR", raising=False)

    with pytest.raises(RuntimeError) as caught:
        AnthropicProvider(model="claude-opus-5", api_key_env="SOME_OTHER_VAR")

    assert "SOME_OTHER_VAR" in str(caught.value)


def test_the_missing_key_error_does_not_echo_any_key(monkeypatch):
    """The message reaches tracebacks, logs and pasted bug reports."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DECOY", "sk-ant-should-not-appear")

    with pytest.raises(RuntimeError) as caught:
        AnthropicProvider(model="claude-opus-5")

    assert "sk-ant-should-not-appear" not in str(caught.value)


def test_generate_passes_the_model_and_budget_through(monkeypatch, anthropic_module):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    provider = AnthropicProvider(model="claude-opus-5")
    provider._client = FakeAnthropicSdkClient("Quarterly. [1]")

    response = provider.generate(system="S", prompt="P", max_tokens=64, temperature=0.0)

    [call] = provider._client.calls
    assert call["model"] == "claude-opus-5"
    assert call["max_tokens"] == 64
    assert response.text == "Quarterly. [1]"


def test_check_is_true_when_the_model_answers(monkeypatch, anthropic_module):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    provider = AnthropicProvider(model="claude-opus-5")
    provider._client = FakeAnthropicSdkClient("OK")

    assert provider.check() is True


def test_check_is_false_when_the_model_says_something_else(monkeypatch, anthropic_module):
    """A key that authenticates against a model that does not exist, or a
    proxy returning an error page, both reach here as a reply that is not
    the word asked for."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    provider = AnthropicProvider(model="claude-opus-5")
    provider._client = FakeAnthropicSdkClient("I cannot help with that.")

    assert provider.check() is False


def test_check_spends_almost_nothing(monkeypatch, anthropic_module):
    """It runs before a whole eval or generate run, so it must stay cheap."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    provider = AnthropicProvider(model="claude-opus-5")
    provider._client = FakeAnthropicSdkClient("ok")

    provider.check()

    [call] = provider._client.calls
    assert call["max_tokens"] <= 16

"""Zardoz's short-call output budgets, and the override that makes a model
comparison possible.

These exist because a budget that is too small does not produce a worse
answer — it produces no answer. `deepseek-v4-pro` exhausted 200 tokens on
resolution and then 1600 on the retry without beginning a reply, while
`deepseek-v4-flash` answered the same case at 200. Comparing those two
models at one fixed budget measures how much they deliberate, not how well
they resolve a question.
"""

from __future__ import annotations

import importlib

import pytest

from policyforge.zardoz import budgets


def _reload(monkeypatch, **env):
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return importlib.reload(budgets)


def test_the_defaults_are_the_measured_ones(monkeypatch):
    """Not the originals. 64/150/200 scored a reasoning model at 95% and 89%
    on tasks it does at 100% given room, and cost more doing it, because a
    truncation bills a second request at eight times the ceiling."""
    reloaded = _reload(
        monkeypatch,
        POLICYFORGE_ROUTING_TOKENS=None,
        POLICYFORGE_EXPANSION_TOKENS=None,
        POLICYFORGE_RESOLUTION_TOKENS=None,
    )

    assert (reloaded.ROUTING_TOKENS, reloaded.EXPANSION_TOKENS, reloaded.RESOLUTION_TOKENS) == (
        800,
        1500,
        2000,
    )


def test_a_budget_can_be_raised_for_a_model_that_deliberates(monkeypatch):
    reloaded = _reload(monkeypatch, POLICYFORGE_RESOLUTION_TOKENS="2000")

    assert reloaded.RESOLUTION_TOKENS == 2000


def test_an_unparseable_budget_is_refused_rather_than_ignored(monkeypatch):
    """Falling back to the default would report a comparison the run did not
    actually perform, which is worse than failing to start."""
    with pytest.raises(ValueError) as caught:
        _reload(monkeypatch, POLICYFORGE_ROUTING_TOKENS="lots")

    assert "POLICYFORGE_ROUTING_TOKENS" in str(caught.value)


def test_a_nonsense_budget_is_refused(monkeypatch):
    with pytest.raises(ValueError):
        _reload(monkeypatch, POLICYFORGE_EXPANSION_TOKENS="0")


def test_blank_is_treated_as_unset(monkeypatch):
    """An exported-but-empty variable is how a shell says "no value", and
    refusing it would make `POLICYFORGE_ROUTING_TOKENS=` a crash."""
    reloaded = _reload(monkeypatch, POLICYFORGE_ROUTING_TOKENS="   ")

    assert reloaded.ROUTING_TOKENS == 800


def test_the_call_sites_read_the_budgets(monkeypatch):
    """The constants are only useful if the three callers actually use them."""
    import inspect

    from policyforge.zardoz import conversation, paraphrase, skills

    # `route` delegates to `_route_by_name`, which is where the routing
    # call — and so the budget — now lives. Argument filling reads the
    # same budget in `_fill_arguments`.
    assert "ROUTING_TOKENS" in inspect.getsource(skills._route_by_name)
    assert "ROUTING_TOKENS" in inspect.getsource(skills._fill_arguments)
    assert "EXPANSION_TOKENS" in inspect.getsource(paraphrase.expand_query)
    assert "RESOLUTION_TOKENS" in inspect.getsource(conversation.resolve_question)


def teardown_module():
    """Leave the module holding real defaults for every other test file."""
    importlib.reload(budgets)

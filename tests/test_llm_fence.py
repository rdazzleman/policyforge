"""llm/fence.py — the delimiter supplied text cannot forge.

Small module, two properties worth pinning: the token is never present in
what it will wrap, and the collision loop that guarantees that actually
loops. The second is the one that would fail silently, so it is tested by
making a collision happen rather than by trusting that one never will.
"""

from __future__ import annotations

from policyforge.llm.fence import fence_token


def test_the_token_is_absent_from_the_text_it_will_wrap():
    text = "A policy page mentioning pf-0000 and BEGIN and END for good measure."
    assert fence_token(text) not in text


def test_every_call_chooses_a_new_token():
    """A token reused across requests is one a document could have learned."""
    tokens = {fence_token("some text") for _ in range(20)}
    assert len(tokens) == 20


def test_it_checks_against_every_text_it_is_given():
    """Passages arrive as separate strings; a token colliding with any of
    them is as broken as one colliding with all."""
    first, second = "harmless", "the second passage"
    token = fence_token(first, second)
    assert token not in first
    assert token not in second


def test_a_colliding_token_is_discarded_and_another_drawn(monkeypatch):
    """The collision loop is the whole guarantee. If it ever stopped
    looping, the fence would still look right and would no longer hold."""
    import policyforge.llm.fence as fence_mod

    drawn = iter(["dead", "dead", "beef"])
    monkeypatch.setattr(fence_mod.secrets, "token_hex", lambda n: next(drawn))

    # A document that already contains the first two tokens this run will draw.
    token = fence_token("a page containing pf-dead, which is unlucky")

    assert token == "pf-beef"


def test_the_token_is_recognisable_as_this_project_s(monkeypatch):
    assert fence_token("").startswith("pf-")

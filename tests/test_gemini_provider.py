"""The Gemini provider, driven at the transport boundary.

Every test here injects a fake `session` and asserts on the JSON that
would go on the wire, or feeds a real `generateContent` reply shape
through the real parsing path. Nothing sets a field on a fake provider and
checks it comes back, because that tests the fake: the truncation
near-miss in 1.2.1 passed review exactly that way while a provider never
populated `stop_reason` at all.

Two shapes here have no analogue in the other providers and are the reason
this file exists rather than a few cases bolted onto an existing one.
Gemini spells a cut-off reply `finishReason: "MAX_TOKENS"` inside the
first candidate. And a blocked or empty reply comes back as **HTTP 200
with no candidates at all**, which every other provider in this project
would express as an error or an empty string.
"""

from __future__ import annotations

import pytest

from policyforge.llm.base import ProviderRejected
from policyforge.llm.gemini_provider import GeminiProvider

KEY_ENV = "POLICYFORGE_GEMINI_TEST_KEY"


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Records the request and returns whatever it was given."""

    def __init__(self, payload=None, status_code=200, text=""):
        self.calls: list[dict] = []
        self.urls: list[str] = []
        self.headers: list[dict] = []
        self._payload = payload if payload is not None else _reply("ok")
        self._status = status_code
        self._text = text

    def post(self, url, *, json, headers, timeout):
        self.calls.append(json)
        self.urls.append(url)
        self.headers.append(headers)
        return FakeResponse(self._payload, self._status, self._text)


def _reply(text, *, finish="STOP", prompt=40, candidates_tokens=12):
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}
        ],
        "usageMetadata": {
            "promptTokenCount": prompt,
            "candidatesTokenCount": candidates_tokens,
        },
        "modelVersion": "gemini-3.6-flash-002",
        "responseId": "abc123",
    }


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "not-a-real-key")


def _provider(session):
    return GeminiProvider(model="gemini-3.6-flash", api_key_env=KEY_ENV, session=session)


# ---- the key ------------------------------------------------------------


def test_a_missing_key_is_refused_before_any_call(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)

    with pytest.raises(RuntimeError) as caught:
        GeminiProvider(model="gemini-3.6-flash", api_key_env=KEY_ENV, session=FakeSession())

    assert KEY_ENV in str(caught.value)
    assert "AI Studio" in str(caught.value)


def test_the_key_travels_in_a_header_and_never_in_the_url():
    """A URL reaches access logs, proxy logs and error messages; a header
    does not. The API accepts the key either way, so this is a choice."""
    session = FakeSession()

    _provider(session).generate(system="s", prompt="p")

    assert session.headers[0]["x-goog-api-key"] == "not-a-real-key"
    assert "not-a-real-key" not in session.urls[0]
    assert "key=" not in session.urls[0]


def test_it_calls_the_ai_studio_endpoint_not_a_vertex_one():
    """`vertex` in this project serves Claude models through Anthropic's
    client on `*-aiplatform.googleapis.com`. This is the other thing."""
    session = FakeSession()

    _provider(session).generate(system="s", prompt="p")

    assert session.urls[0] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    )


# ---- the request shape --------------------------------------------------


def test_the_system_instruction_is_a_field_not_a_first_user_turn():
    session = FakeSession()

    _provider(session).generate(system="You are careful.", prompt="Draft it.", max_tokens=99)

    sent = session.calls[0]
    assert sent["system_instruction"] == {"parts": [{"text": "You are careful."}]}
    assert sent["contents"] == [{"role": "user", "parts": [{"text": "Draft it."}]}]
    assert sent["generationConfig"]["maxOutputTokens"] == 99


def test_an_empty_system_prompt_sends_no_system_instruction():
    session = FakeSession()

    _provider(session).generate(system="", prompt="p")

    assert "system_instruction" not in session.calls[0]


def test_a_schema_call_sends_the_schema_and_the_json_mime_type():
    """This is the half of `supports_schema()` that can be proven without a
    live call: that the request carries the constraint."""
    session = FakeSession(_reply('{"ok": true}'))
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    _provider(session).generate_json(system="s", prompt="p", schema=schema)

    config = session.calls[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == schema


def test_a_plain_call_sends_no_schema():
    session = FakeSession()

    _provider(session).generate(system="s", prompt="p")

    assert "responseSchema" not in session.calls[0]["generationConfig"]


# ---- the reply shape ----------------------------------------------------


def test_the_reply_is_read_into_the_projects_own_terms():
    session = FakeSession(_reply("Drafted.", prompt=7, candidates_tokens=3))

    response = _provider(session).generate(system="s", prompt="p")

    assert response.text == "Drafted."
    assert response.model == "gemini-3.6-flash-002"
    assert response.input_tokens == 7
    assert response.output_tokens == 3
    assert response.stop_reason == "STOP"
    assert response.request_id == "abc123"


def test_multiple_parts_are_joined_in_order():
    """A reply can arrive split across parts; concatenating in order is what
    the caller means by "the text"."""
    payload = _reply("")
    payload["candidates"][0]["content"]["parts"] = [{"text": "one "}, {"text": "two"}]
    session = FakeSession(payload)

    assert _provider(session).generate(system="s", prompt="p").text == "one two"


def test_a_cut_off_reply_reports_gemini_s_own_signal():
    """`MAX_TOKENS`, which is what `LLMResponse.truncated` matches on."""
    session = FakeSession(_reply("half a senten", finish="MAX_TOKENS"))

    response = _provider(session).generate(system="s", prompt="p", max_tokens=8)

    assert response.stop_reason == "MAX_TOKENS"
    assert response.truncated


def test_a_blocked_reply_has_no_candidates_and_is_empty_not_a_crash():
    """Gemini's safety block is an HTTP 200 with no candidate at all — a
    shape no other provider here produces. It must read as an empty reply,
    which `effort.document_text` refuses with `EmptyReply`, rather than as
    an exception from indexing a missing list."""
    session = FakeSession({"usageMetadata": {"promptTokenCount": 40}, "modelVersion": "g"})

    response = _provider(session).generate(system="s", prompt="p")

    assert response.text == ""
    assert response.stop_reason is None
    assert response.output_tokens is None


def test_a_candidate_with_no_parts_is_also_empty():
    payload = {"candidates": [{"content": {"role": "model"}, "finishReason": "SAFETY"}]}
    session = FakeSession(payload)

    response = _provider(session).generate(system="s", prompt="p")

    assert response.text == ""
    assert response.stop_reason == "SAFETY"


# ---- errors -------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 413, 422])
def test_a_request_refused_as_written_raises_provider_rejected(status):
    session = FakeSession({}, status_code=status, text="maxOutputTokens too large")

    with pytest.raises(ProviderRejected) as caught:
        _provider(session).generate(system="s", prompt="p")

    assert "maxOutputTokens too large" in str(caught.value)


@pytest.mark.parametrize("status", [429, 401, 403, 500, 503])
def test_everything_else_stays_a_plain_error(status):
    """A throttle clears by waiting and a 403 is the key. Budget advice on
    either would send the operator to change the wrong thing."""
    session = FakeSession({}, status_code=status, text="nope")

    with pytest.raises(RuntimeError) as caught:
        _provider(session).generate(system="s", prompt="p")

    assert not isinstance(caught.value, ProviderRejected)
    assert str(status) in str(caught.value)


# ---- what it says about itself ------------------------------------------


def test_the_capability_flags_describe_what_this_provider_sends():
    """Bedrock declares none at all, so a capable model served through it
    silently got no effort level, no cache hint and no schema. The lesson
    taken here is to declare what this file actually sends."""
    provider = _provider(FakeSession())

    assert provider.supports_schema() is True
    assert provider.supports_effort() is False
    assert provider.supports_caching() is False
    assert provider.supports_grounding() is False
    assert provider.supports_batch() is False


def test_effort_and_cache_arguments_are_accepted_and_ignored():
    """A caller that honours the flags never passes these. One that does not
    should get a plain call rather than a crash, and must not have them
    quietly take effect — in particular `effort="high"` must not buy
    thinking, since this provider does not offer thinking to the caller."""
    session = FakeSession()

    _provider(session).generate(system="s", prompt="p", effort="high", cache=True, cache_prefix="x")

    config = session.calls[0]["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert "cachedContent" not in session.calls[0]


# ---- the budget means tokens of answer ----------------------------------


def test_every_call_disables_thinking_so_the_budget_is_the_answer():
    """Measured live: without this, a 64-token budget spent 60 on reasoning
    and returned no content at all with `finishReason: MAX_TOKENS` — the
    truncation guard firing correctly on a call that could never produce
    anything. With it, the same budget returned 60 tokens of prose.

    A caller asking for `max_tokens` means tokens of answer. Translating
    that into the vendor's meaning is the adapter's job; the alternative is
    every caller and every budget learning that one vendor thinks."""
    session = FakeSession()
    provider = _provider(session)

    provider.generate(system="s", prompt="p", max_tokens=64)
    provider.generate_json(system="s", prompt="p", schema={"type": "object"}, max_tokens=64)

    for sent in session.calls:
        assert sent["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
        assert sent["generationConfig"]["maxOutputTokens"] == 64


# ---- billed but not returned --------------------------------------------


def test_thoughts_tokens_are_recorded_as_hidden_output():
    """The operator paid for these and cannot read them. Live numbers."""
    payload = _reply("A security control is...")
    payload["usageMetadata"] = {
        "promptTokenCount": 7,
        "candidatesTokenCount": 813,
        "thoughtsTokenCount": 842,
        "totalTokenCount": 1662,
    }
    session = FakeSession(payload)

    response = _provider(session).generate(system="s", prompt="p")

    assert response.output_tokens == 813
    assert response.hidden_output_tokens == 842


def test_an_absent_thoughts_count_is_unknown_not_zero():
    """With thinking disabled the field is absent from `usageMetadata`
    entirely, which is what a live call returns. It must read as "the API
    did not say" rather than "none were billed": we believe it is zero, and
    believing is not being told, and a model that ignored the setting would
    be invisible if we asserted zero on its behalf."""
    payload = _reply("A security control is...")
    payload["usageMetadata"] = {"promptTokenCount": 7, "candidatesTokenCount": 60}
    session = FakeSession(payload)

    response = _provider(session).generate(system="s", prompt="p")

    assert response.hidden_output_tokens is None
    assert response.output_tokens == 60


def test_an_absent_candidates_count_is_unknown_not_zero():
    """A reply cut off before its first content token omits
    `candidatesTokenCount` altogether — observed live. `None` is the honest
    answer and `0` would be a measurement nobody made. This holds by
    deliberate use of `.get()` rather than by luck, and this test is what
    stops a default being tidied in."""
    payload = {
        "candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}],
        "usageMetadata": {"promptTokenCount": 7, "thoughtsTokenCount": 60},
        "modelVersion": "gemini-3.6-flash",
    }
    session = FakeSession(payload)

    response = _provider(session).generate(system="s", prompt="p", max_tokens=64)

    assert response.output_tokens is None
    assert response.hidden_output_tokens == 60
    assert response.text == ""
    assert response.truncated


def test_grounded_and_batch_calls_refuse_rather_than_degrade():
    provider = _provider(FakeSession())

    with pytest.raises(NotImplementedError):
        provider.generate_grounded(system="s", prompt="p", documents=[])
    with pytest.raises(NotImplementedError):
        provider.generate_batch([])

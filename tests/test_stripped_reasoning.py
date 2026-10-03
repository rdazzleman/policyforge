"""How much of a reply was reasoning we removed before recording it.

The pair to `hidden_output_tokens`, from the other side. That field is
what the vendor says it billed and did not return; this is what arrived
and was not kept. Until both existed, neither was recoverable after the
fact: a reply came back, its inline `<think>` preamble was cut, and the
ledger stored the remainder with nothing to say a cut had happened. So
"did this model spend most of its reply thinking?" needed a fresh run to
answer, and a fresh run is a different run.

Characters rather than tokens, deliberately: characters are exact here,
and tokens would need a tokenizer this project does not carry for every
model. The field answers "how much was removed", not "what it cost".
"""

from __future__ import annotations

from policyforge.llm._inline_thinking import answer_and_stripped, answer_of
from policyforge.llm.base import LLMResponse
from policyforge.llm.ledger import CallRecord

# ---- the helper ---------------------------------------------------------


def test_nothing_to_strip_is_zero_not_none():
    """The strip ran and found nothing. That is a measurement."""
    answer, stripped = answer_and_stripped("Accounts are reviewed quarterly.")

    assert answer == "Accounts are reviewed quarterly."
    assert stripped == 0


def test_a_closed_think_block_is_counted():
    content = "<think>The standard says quarterly.</think>Accounts are reviewed quarterly."

    answer, stripped = answer_and_stripped(content)

    assert answer == "Accounts are reviewed quarterly."
    assert stripped == len(content) - len(answer)
    assert stripped == 43  # 7 + 28 + 8: the tags and what they enclose


def test_an_unclosed_think_block_counts_the_whole_reply():
    """The case the strip exists for: a budget spent entirely inside an
    unterminated block. Everything was reasoning, so everything is counted
    and the answer is empty — which `needs_more_room` then recognises."""
    content = "<think>Let me work through the retention requirement step by"

    answer, stripped = answer_and_stripped(content)

    assert answer == ""
    assert stripped == len(content)


def test_the_count_includes_whitespace_the_strip_trims():
    """`answer_of` strips surrounding whitespace too, so the subtraction
    counts it. Reporting only the tag contents would understate what was
    removed and would not reconcile against the raw length."""
    content = "<think>x</think>\n\n  Answer.  "

    answer, stripped = answer_and_stripped(content)

    assert answer == "Answer."
    assert stripped == len(content) - len("Answer.")


def test_it_agrees_with_answer_of_on_every_shape():
    """The counting form must not change what the answer is — only report
    how much went. `answer_of` is used directly elsewhere and by tests."""
    for content in [
        None,
        "",
        "plain",
        "<think>a</think>b",
        "<think>unterminated",
        "before<think>middle</think>after",
    ]:
        answer, stripped = answer_and_stripped(content)
        assert answer == answer_of(content)
        assert stripped == len(content or "") - len(answer)


def test_none_content_is_empty_and_removes_nothing():
    assert answer_and_stripped(None) == ("", 0)


# ---- the response and the ledger ----------------------------------------


def test_the_response_default_is_unknown_not_zero():
    """A provider that never strips must not claim it stripped nothing.
    Same rule as the token counts: a zero nobody measured is a claim."""
    assert LLMResponse(text="x", model="m").stripped_reasoning_chars is None


def test_the_ledger_carries_it():
    record = CallRecord(
        timestamp="2026-09-18T12:00:00Z",
        provider="local",
        provider_class="local",
        model="qwen3:14b",
        subject="s",
        site="zardoz.answer",
        stripped_reasoning_chars=1284,
    )

    assert record.stripped_reasoning_chars == 1284
    assert (
        CallRecord(
            timestamp="t",
            provider="anthropic",
            provider_class="third-party",
            model="m",
            subject=None,
            site=None,
        ).stripped_reasoning_chars
        is None
    )


def test_the_two_fields_answer_different_questions():
    """Both can be set on one response and they are not the same number:
    one is what the vendor withheld, the other what we cut from what it
    sent. A reader who conflates them would double-count."""
    response = LLMResponse(
        text="Answer.",
        model="m",
        output_tokens=12,
        hidden_output_tokens=292,
        stripped_reasoning_chars=1284,
    )

    assert response.hidden_output_tokens == 292
    assert response.stripped_reasoning_chars == 1284


# ---- through a provider, at the transport boundary -----------------------


def _openai_session(content, finish="stop"):
    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "model": "qwen3:14b",
                "choices": [{"message": {"content": content}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 40},
            }

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, url, *, json, headers, timeout):
            self.calls.append(json)
            return Response()

    return Session()


def test_the_compat_provider_reports_what_it_stripped():
    """Driven at the transport boundary: a real inline-reasoning reply
    through the real parsing path, not a field set on a fake."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    content = "<think>Quarterly, per section 4.2.</think>Accounts are reviewed quarterly."
    provider = OpenAICompatProvider(
        model="qwen3:14b",
        base_url="http://localhost:11434/v1",
        session=_openai_session(content),
    )

    response = provider.generate(system="", prompt="p")

    assert response.text == "Accounts are reviewed quarterly."
    assert response.stripped_reasoning_chars == len(content) - len(response.text)


def test_a_reply_with_no_reasoning_reports_zero_through_the_provider():
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    provider = OpenAICompatProvider(
        model="qwen3:14b",
        base_url="http://localhost:11434/v1",
        session=_openai_session("Accounts are reviewed quarterly."),
    )

    assert provider.generate(system="", prompt="p").stripped_reasoning_chars == 0

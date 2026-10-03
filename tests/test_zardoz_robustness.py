"""Pathological input to the functions that consume model output.

Everything here is free — no provider, no network. The point is that
`check_answer`, `parse_expansion` and retrieval all parse text nobody
sanitised: a model reply, a Confluence page body, whatever someone typed at
the prompt. A crash in any of them takes down a session that was working,
and the inputs that cause one are exactly the inputs nobody writes a case
for.

Nothing here asserts a particular answer. The claim is narrower and worth
making on its own: these functions return, and return the right shape.
"""

from __future__ import annotations

import pytest

from policyforge.zardoz.answer import answer_question, check_answer
from policyforge.zardoz.conversation import (
    Conversation,
    Turn,
    looks_like_a_follow_up,
    resolve_question,
)
from policyforge.zardoz.corpus import TRUSTED, Corpus, CorpusDocument
from policyforge.zardoz.paraphrase import parse_expansion
from policyforge.zardoz.retrieve import build_index, tokenize

NASTY = [
    "",
    "   ",
    "\n\n\t",
    "[1]" * 500,
    "[99999999999999999999999999]",
    "[0] [1.5] [ 1 ] [-1]",
    '"' * 50,
    "“" * 20,
    "\\" * 100,
    "(?i)(a+)+$",
    "​​​",
    "‮reversed text‬",
    "\U0001f600" * 100,
    "a" * 20000,
    "\x01\x02\x03",
    "AC-6(5) " * 200,
    "INSUFFICIENT_CONTEXT and then some more text",
    "| a | b |\n| - | - |\n" * 50,
    "# " * 500,
    "́́́combining",
]


def _passages(body: str = "Accounts are recertified quarterly. [NIST AC-2]"):
    corpus = Corpus(
        documents=[
            CorpusDocument(
                doc_id="1",
                title="Access Control Standard",
                space="",
                confidence=TRUSTED,
                source="markdown",
                body=f"# Access Control Standard\n\n## 4.1 Review\n\n{body}\n",
            )
        ]
    )
    return build_index(corpus).search("how often are accounts recertified?", limit=4)


class _Returns:
    """A provider that replies with whatever it was handed."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def generate(self, **kwargs):
        class R:
            text = self.reply

        return R()


@pytest.mark.parametrize("text", NASTY)
def test_check_answer_survives_any_model_reply(text):
    cited, warnings = check_answer(text, _passages(), "how often are accounts recertified?")

    assert isinstance(cited, list)
    assert all(isinstance(n, int) for n in cited)
    assert isinstance(warnings, list)
    assert all(isinstance(w, str) for w in warnings)


@pytest.mark.parametrize("text", NASTY)
def test_check_answer_survives_a_pathological_passage_too(text):
    """The passage is not model output, but it is not clean either — it is
    whatever was in a Confluence page or a markdown file."""
    check_answer("The standard requires review. [1]", _passages(text), "anything")


@pytest.mark.parametrize("text", NASTY)
def test_parse_expansion_survives_any_model_reply(text):
    terms = parse_expansion(text)

    assert isinstance(terms, list)
    assert all(isinstance(t, str) and t for t in terms)
    assert len(terms) <= 12


@pytest.mark.parametrize("text", NASTY)
def test_retrieval_survives_any_query(text):
    passages = build_index(
        Corpus(
            documents=[
                CorpusDocument(
                    doc_id="1",
                    title="S",
                    space="",
                    confidence=TRUSTED,
                    source="markdown",
                    body="# S\n\n## H\n\nBody.\n",
                )
            ]
        )
    ).search(text, limit=4)

    assert isinstance(passages, list)


@pytest.mark.parametrize("text", NASTY)
def test_retrieval_survives_any_document_body(text):
    corpus = Corpus(
        documents=[
            CorpusDocument(
                doc_id="1",
                title="S",
                space="",
                confidence=TRUSTED,
                source="markdown",
                body=text,
            )
        ]
    )

    assert isinstance(build_index(corpus).search("anything", limit=4), list)


@pytest.mark.parametrize("text", NASTY)
def test_tokenize_survives_anything(text):
    assert all(isinstance(t, str) for t in tokenize(text))


@pytest.mark.parametrize("text", NASTY)
def test_answering_survives_a_pathological_reply(text):
    answer = answer_question("how often?", _passages(), _Returns(text))

    assert isinstance(answer.text, str)
    assert isinstance(answer.refused, bool)
    assert isinstance(answer.warnings, list)


@pytest.mark.parametrize("text", NASTY)
def test_resolution_survives_a_pathological_rewrite(text):
    history = Conversation(
        [Turn(question="how often are accounts recertified?", answer="Quarterly. [1]")]
    )

    resolved, rewritten = resolve_question("who owns that?", history, _Returns(text))

    assert isinstance(resolved, str) and resolved.strip()
    assert isinstance(rewritten, bool)


@pytest.mark.parametrize("text", NASTY)
def test_follow_up_detection_survives_anything(text):
    assert isinstance(looks_like_a_follow_up(text), bool)

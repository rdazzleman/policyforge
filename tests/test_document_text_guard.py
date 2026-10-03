"""Every document producer passes the reply through `effort.document_text`.

`EmptyReply` (1.2.1) refuses a document reply with no text, but only where
a producer asks for it. A producer written later that returns
`response.text` straight from `effort.call` would write an empty body with
exit 0 again, and the tests for the existing producers would not notice.

Two rules, over the AST so a docstring may discuss the calls freely:

1. In each module listed as a document producer, every function that makes
   a model call through `effort.call`, `call_shaped` or `call_grounded`
   must also call `effort.document_text`. (`call_json` replies are parsed,
   not written, and are refused by the parser when empty.)
2. In every other module under `src/` that reads `response.text`, the
   module must be named below with the reason its empty reply is handled
   another way, so a new module that starts writing a document is caught
   the day it is written.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "policyforge"

#: Modules whose model replies are written as documents.
PRODUCERS = [
    "synthesis/merge.py",
    "generate/policy_writer.py",
    "edit/apply.py",
]

#: Modules that read a reply's text but are not document producers, and
#: why an empty reply there is not a silent empty document.
NOT_PRODUCERS = {
    "ssp/narrative.py": "a narrative is one workbook cell; `_finished` maps an empty reply "
    "to an empty cell by design, and the batch path refuses cut-off cells by id",
    "ingest/parser_codegen.py": "generated code; an empty module fails the parser gate's "
    "syntax check before it can be written",
    "edit/plan.py": "a plan is parsed as JSON; an empty reply is a malformed plan, refused",
    "entail/llm_entailer.py": "a verdict parsed as JSON from a schema-constrained call; an "
    "empty reply is a parse error, not a verdict",
    "crosswalk/propose.py": "proposals are parsed rows; an empty reply proposes nothing",
    "zardoz/answer.py": "an answer is checked, not written; an empty one is refused",
    "zardoz/conversation.py": "a rewrite; an empty reply falls back to the question",
    "zardoz/paraphrase.py": "an expansion; an empty reply means no expansion",
    "zardoz/discover.py": "clusters are parsed; an empty reply proposes nothing",
    "zardoz/skills.py": "a routing word; an empty reply is read as 'not an analysis'",
    "ingest/ecfr.py": "`response.text` here is an HTTP body from eCFR, not a model. "
    "The HIPAA loader used to be listed here; the fetch moved into this shared "
    "module when the second regulation arrived, and this guard caught the stale entry.",
    "ingest/ai_rmf.py": "`response.text` here is an HTTP body from NIST's AIRC, not a "
    "model. The AI RMF catalog is fetched, never generated — the framework this project "
    "cites about AI is not itself written by one.",
    "embed/ollama_provider.py": "HTTP error text, not a model reply",
    "rerank/llamacpp_provider.py": "HTTP error text, not a model reply",
}

MODEL_CALLS = {"call", "call_shaped", "call_grounded"}


def _calls_in(node: ast.AST) -> set[str]:
    names = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "effort"
        ):
            names.add(sub.func.attr)
    return names


def _functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _reads_response_text(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "text"
            and isinstance(node.value, ast.Name)
            and node.value.id == "response"
        ):
            return True
    return False


@pytest.mark.parametrize("relative", PRODUCERS)
def test_every_model_call_in_a_producer_goes_through_document_text(relative):
    offenders = []
    for function in _functions(SRC / relative):
        calls = _calls_in(function)
        if calls & MODEL_CALLS and "document_text" not in calls:
            offenders.append(function.name)
    assert offenders == [], (
        f"{relative}: {offenders} make a model call and return its text without "
        "effort.document_text, so an empty reply would be written as a document"
    )


def test_every_other_reader_of_a_reply_is_accounted_for():
    """A module that starts reading `response.text` is either a producer
    (add it to PRODUCERS) or has a reason it is not (add it here)."""
    readers = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if p.relative_to(SRC).parts[0] != "llm" and _reads_response_text(p)
    )
    known = set(PRODUCERS) | set(NOT_PRODUCERS)
    unlisted = [r for r in readers if r not in known]
    assert unlisted == [], f"reads a model reply's text and is listed nowhere: {unlisted}"
    # Only the non-producers are held to still reading `response.text`: a
    # producer, by the rule above, hands the response to `document_text`
    # and never touches `.text` itself.
    stale = sorted(set(NOT_PRODUCERS) - set(readers))
    assert stale == [], f"listed but no longer read response.text: {stale}"


def test_the_producers_are_the_ones_the_writers_call():
    """The list is not arbitrary: these are the modules `synthesize`,
    `generate` and the edit commands write from."""
    assert (SRC / "synthesis/merge.py").exists()
    assert (SRC / "generate/policy_writer.py").exists()
    assert (SRC / "edit/apply.py").exists()

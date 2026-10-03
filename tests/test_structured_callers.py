"""The three callers that were parsing prose, now asking for a shape.

What is worth testing here is not that a schema works — that is the API's
job — but that the *fallback* still works, since the fallback is the whole
reason `call_shaped` exists rather than `call_json`. Each test below is run
twice: once against a provider that can be held to a schema, once against
one that cannot, with the same expected outcome. A change that quietly makes
a local model unsupported fails here.
"""

from __future__ import annotations

import json

import pytest

from policyforge.edit.plan import PLAN_SCHEMA, build_edit_plan
from policyforge.llm import effort
from policyforge.llm.base import LLMResponse
from policyforge.zardoz.discover import (
    CLUSTER_SCHEMA,
    CLUSTER_SYSTEM_PROMPT,
    CLUSTER_SYSTEM_PROMPT_JSON,
    _parse_clusters,
    cluster_leftovers,
)


class Prose:
    """A provider that cannot be constrained — an Ollama server, in effect."""

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def generate(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(text=self.text, model="local/whatever")


class Shaped(Prose):
    """A provider that will hold the reply to the schema it is given."""

    def supports_schema(self) -> bool:
        return True

    def supports_effort(self) -> bool:
        return True

    def generate_json(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(text=self.text, model="claude-opus-5")


DOCUMENT = """# Access Control Standard

## Scope

Applies to all production systems.

## Review

Accounts are reviewed quarterly. [NIST AC-2]
"""

PLAN_JSON = {
    "steps": [
        {
            "kind": "modify",
            "target": "Review",
            "summary": "Change the review cadence to monthly.",
            "rationale": "The instruction asks for monthly reviews.",
        }
    ],
    "risks": [],
    "out_of_scope": [],
}


@pytest.mark.parametrize("provider_class", [Prose, Shaped])
def test_plan_reads_the_same_either_way(provider_class):
    """The same plan comes back whether the shape was enforced or asked for."""
    provider = provider_class(json.dumps(PLAN_JSON))
    plan = build_edit_plan(
        "Make the account review monthly.",
        DOCUMENT,
        provider,
        page_title="Access Control Standard",
    )
    assert [s.kind for s in plan.steps] == ["modify"]
    assert plan.steps[0].target == "Review"
    assert not plan.risks


def test_plan_sends_the_schema_when_it_can_be_honoured():
    provider = Shaped(json.dumps(PLAN_JSON))
    build_edit_plan("Make it monthly.", DOCUMENT, provider)
    assert provider.calls[0]["schema"] is PLAN_SCHEMA
    assert provider.calls[0]["effort"] == effort.EDITING


def test_plan_sends_no_schema_to_a_provider_that_would_drop_it():
    """`generate` has no `schema` parameter, so passing one is a TypeError.

    Which is the point: the fallback has to call the old signature, not the
    new one with an argument the provider silently ignores.
    """
    provider = Prose(json.dumps(PLAN_JSON))
    build_edit_plan("Make it monthly.", DOCUMENT, provider)
    assert "schema" not in provider.calls[0]


def test_plan_still_recovers_a_fenced_object_from_a_local_model():
    """The lenient parser is not made redundant by the schema."""
    provider = Prose("Here is the plan:\n\n```json\n" + json.dumps(PLAN_JSON) + "\n```\n")
    plan = build_edit_plan("Make it monthly.", DOCUMENT, provider)
    assert plan.steps[0].summary == "Change the review cadence to monthly."


class Page:
    def __init__(self, title: str):
        self.title = title
        self.storage_body = "<p>No control ids here.</p>"


def test_clustering_reads_both_shapes():
    titles = {"Access Review Runbook", "Joiner Mover Leaver"}
    as_lines = "Access Review: Access Review Runbook | Joiner Mover Leaver"
    as_json = json.dumps(
        {"topics": [{"name": "Access Review", "titles": sorted(titles)}]},
    )
    assert _parse_clusters(as_lines, titles) == [
        ("Access Review", ["Access Review Runbook", "Joiner Mover Leaver"])
    ]
    assert _parse_clusters(as_json, titles) == [
        ("Access Review", ["Access Review Runbook", "Joiner Mover Leaver"])
    ]


def test_clustering_keeps_a_title_containing_the_line_delimiters():
    """The failure the schema is actually for.

    "Backup | Restore: Weekly" is one page. Under the line format it is a
    topic name and two members, none of which exist, so the page is dropped
    silently — indistinguishable from the model choosing to leave it out.
    """
    titles = {"Backup | Restore: Weekly", "Restore Testing"}
    as_json = json.dumps(
        {"topics": [{"name": "Backup and Restore", "titles": sorted(titles)}]},
    )
    assert _parse_clusters(as_json, titles) == [
        ("Backup and Restore", ["Backup | Restore: Weekly", "Restore Testing"])
    ]


def test_clustering_drops_titles_the_model_invented():
    titles = {"Access Review Runbook"}
    as_json = json.dumps(
        {
            "topics": [
                {"name": "Access Review", "titles": ["Access Review Runbook", "Invented Page"]}
            ]
        }
    )
    assert _parse_clusters(as_json, titles) == [("Access Review", ["Access Review Runbook"])]


@pytest.mark.parametrize(
    ("provider_class", "reply"),
    [
        (Prose, "Access Review: Access Review Runbook | Joiner Mover Leaver"),
        (
            Shaped,
            json.dumps(
                {
                    "topics": [
                        {
                            "name": "Access Review",
                            "titles": ["Access Review Runbook", "Joiner Mover Leaver"],
                        }
                    ]
                }
            ),
        ),
    ],
)
def test_cluster_leftovers_groups_either_way(provider_class, reply):
    pages = [Page("Access Review Runbook"), Page("Joiner Mover Leaver")]
    topics, leftovers = cluster_leftovers(pages, provider_class(reply))
    assert [t.name for t in topics] == ["Access Review"]
    assert not leftovers


def test_cluster_prompt_matches_the_shape_that_will_be_enforced():
    """A forced grammar plus a contradicting instruction is a worse prompt."""
    shaped = Shaped(json.dumps({"topics": []}))
    cluster_leftovers([Page("A Page")], shaped)
    assert shaped.calls[0]["system"] == CLUSTER_SYSTEM_PROMPT_JSON
    assert shaped.calls[0]["schema"] is CLUSTER_SCHEMA
    assert "one topic per line" not in shaped.calls[0]["system"]

    prose = Prose("")
    cluster_leftovers([Page("A Page")], prose)
    assert prose.calls[0]["system"] == CLUSTER_SYSTEM_PROMPT
    assert "one topic per line" in prose.calls[0]["system"]


def test_both_cluster_prompts_carry_the_same_rules():
    """The two wordings differ in one paragraph, not in what is asked."""
    for rule in ("Never invent a page", "at most once", "after the process"):
        assert rule in CLUSTER_SYSTEM_PROMPT
        assert rule in CLUSTER_SYSTEM_PROMPT_JSON


def test_call_shaped_refuses_nothing_that_call_json_would():
    """`call_shaped` is a fallback, `call_json` is a guarantee. Keep them apart."""

    class Unconstrainable:
        def generate(self, **kwargs):
            return LLMResponse(text="prose", model="local")

    provider = Unconstrainable()
    assert effort.call_shaped(provider, schema={}, system="s", prompt="p").text == "prose"
    with pytest.raises((NotImplementedError, AttributeError)):
        effort.call_json(provider, schema={}, system="s", prompt="p")

"""The rule splitter, tested offline.

`mutate.py` decides what counts as evidence about the eval suite, so a bug
here is worse than a bug in a case: a splitter that quietly dropped two
rules instead of one would report the survivors as unguarded and invite
deleting a rule that was doing its job.
"""

from __future__ import annotations

import pytest

from evals.mutate import rules, summarize, without_rule

PROMPT = """You do a thing.

Rules, in priority order:

1. First rule. It has a second sentence.
2. Second rule, which wraps onto
   a continuation line that is indented.
3. Third rule mentions a version like 1. 2. 3. inline.
4. Fourth rule."""


def test_rules_are_split_on_the_number_at_the_start_of_a_line():
    assert [n for n, _ in rules(PROMPT)] == [1, 2, 3, 4]


def test_a_wrapped_rule_keeps_its_continuation():
    text = dict(rules(PROMPT))[2]
    assert "continuation line" in text


def test_numbers_inside_prose_do_not_start_a_rule():
    """A rule whose text contains "2." must not split into two rules."""
    text = dict(rules(PROMPT))[3]
    assert "1. 2. 3. inline" in text
    assert len(rules(PROMPT)) == 4


def test_without_rule_removes_exactly_one():
    cut = without_rule(PROMPT, 2)

    assert [n for n, _ in rules(cut)] == [1, 3, 4]
    assert "continuation line" not in cut
    assert "First rule" in cut and "Fourth rule" in cut


def test_without_rule_keeps_the_preamble():
    assert without_rule(PROMPT, 1).startswith("You do a thing.")


def test_the_remaining_rules_keep_their_original_numbers():
    """Renumbering would be a second edit the experiment did not ask for,
    and the answering prompt says "in priority order", so a rule's number
    is part of what it means."""
    assert [n for n, _ in rules(without_rule(PROMPT, 1))] == [2, 3, 4]


def test_removing_the_last_rule_works():
    cut = without_rule(PROMPT, 4)

    assert [n for n, _ in rules(cut)] == [1, 2, 3]
    assert "Fourth rule" not in cut


def test_an_unknown_rule_is_an_error_not_a_silent_no_op():
    """Silently returning the prompt unchanged would report every rule as
    unguarded, since nothing was ever actually mutated."""
    with pytest.raises(KeyError):
        without_rule(PROMPT, 99)


def test_removals_compose_for_pair_mutation():
    cut = without_rule(without_rule(PROMPT, 1), 4)

    assert [n for n, _ in rules(cut)] == [2, 3]


@pytest.mark.parametrize(
    "module_path, attribute",
    [
        ("policyforge.zardoz.answer", "SYSTEM_PROMPT"),
        ("policyforge.zardoz.skills", "ROUTER_SYSTEM_PROMPT"),
        ("policyforge.zardoz.conversation", "RESOLVE_SYSTEM_PROMPT"),
        ("policyforge.zardoz.paraphrase", "EXPANSION_SYSTEM_PROMPT"),
    ],
)
def test_every_shipped_prompt_splits_into_consecutive_rules(module_path, attribute):
    """Catch a prompt edit that breaks the numbering offline. A prompt whose
    rules stopped parsing would sweep as zero rules and report nothing."""
    import importlib

    prompt = getattr(importlib.import_module(module_path), attribute)
    numbers = [n for n, _ in rules(prompt)]

    assert numbers, f"{attribute} parsed as having no rules"
    assert numbers == list(range(1, len(numbers) + 1)), numbers


def test_summarize_gives_one_short_clause():
    assert summarize("1. Ground every claim. Each sentence must cite.") == "Ground every claim."
    assert len(summarize("2. " + "word " * 60)) <= 64

"""The programme's scope facts (22, part 3): what is read, what refuses, and
what "not declared" means."""

from __future__ import annotations

import pytest
import yaml

from policyforge.topics.scope_facts import (
    ScopeFactError,
    ScopeFacts,
    load_scope_facts,
    parse_scope_facts,
)


def _facts(text: str) -> ScopeFacts:
    return load_scope_facts(yaml.safe_load(text))


def test_a_config_with_no_programme_declares_nothing():
    facts = load_scope_facts({})

    assert facts == ScopeFacts()
    assert facts.declared() == {}
    assert facts.undeclared() == [
        "builds_software",
        "own_facilities",
        "personal_data",
        "baseline",
        "ai",
    ]


def test_the_five_facts_are_read_as_written():
    facts = _facts(
        """
programme:
  catalogs: [nist-800-53-r5]
  builds_software: true
  own_facilities: false
  personal_data: phi
  baseline: 800-53
  ai: builder
"""
    )

    assert facts.builds_software is True
    assert facts.own_facilities is False
    assert (facts.personal_data, facts.baseline, facts.ai) == ("phi", "800-53", "builder")
    assert facts.undeclared() == []


def test_false_is_a_declared_answer_and_is_not_undeclared():
    facts = _facts("programme: {own_facilities: false}")

    assert facts.own_facilities is False
    assert "own_facilities" not in facts.undeclared()
    assert facts.declared() == {"own_facilities": False}


def test_the_word_none_is_an_answer_and_a_null_is_not():
    """`ai: none` is a string in YAML and means no AI; `ai: ~` (or a key with
    no value) is null and means nobody said."""
    said = _facts("programme: {ai: none}")
    null = _facts("programme: {ai: ~}")

    assert said.ai == "none" and "ai" not in said.undeclared()
    assert null.ai is None and "ai" in null.undeclared()


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("programme: {builds_software: yes please}", "builds_software"),
        ("programme: {builds_software: 'true'}", "builds_software"),
        ("programme: {own_facilities: 1}", "own_facilities"),
        ("programme: {personal_data: ephi}", "personal_data"),
        ("programme: {baseline: iso27001}", "baseline"),
        ("programme: {ai: both}", "ai"),
        ("programme: {ai: 3}", "ai"),
    ],
)
def test_a_value_outside_the_vocabulary_refuses_naming_the_key(text, name):
    with pytest.raises(ScopeFactError) as raised:
        _facts(text)

    assert f"programme.{name}" in str(raised.value)


def test_a_programme_that_is_not_a_mapping_refuses():
    with pytest.raises(ScopeFactError, match="must be a mapping"):
        parse_scope_facts(["nist-800-53-r5"])


def test_case_and_space_in_a_word_are_forgiven():
    assert _facts("programme: {personal_data: ' PHI '}").personal_data == "phi"


def test_the_reads_the_rule_makes():
    assert _facts("programme: {personal_data: pii}").handles_personal_data
    assert not _facts("programme: {personal_data: pii}").handles_phi
    assert _facts("programme: {personal_data: phi}").handles_phi
    assert not ScopeFacts().handles_personal_data
    for baseline in ("800-53", "fedramp", "govramp"):
        assert _facts(f"programme: {{baseline: {baseline}}}").has_assessed_baseline
    assert not _facts("programme: {baseline: 'none'}").has_assessed_baseline

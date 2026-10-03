"""zardoz discover tests — proposing a registry from an uncatalogued space.

The property worth defending is that the proposal is *checkable*. Most rows
should come from a signal a reviewer can verify by looking at the titles,
the model should only touch what the conventions could not reach, and every
owner should be blank — because nothing in a page reliably says which team
is accountable, and a wrong owner in a compliance artifact gets believed
while a blank one gets filled in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from policyforge.zardoz.discover import (
    UNASSIGNED,
    discover_topics,
    group_by_convention,
    render_registry,
    split_title,
)


@dataclass
class FakePage:
    title: str
    storage_body: str = ""
    id: str = "1"
    version: int = 1
    webui_url: str = ""
    labels: list = field(default_factory=list)
    ancestors: list = field(default_factory=list)


@dataclass
class FakeResponse:
    text: str
    model: str = "fake"


class ScriptedProvider:
    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        return FakeResponse(self.reply)

    def check(self) -> bool:
        return True


def _space():
    return [
        FakePage("Access Control Policy", "<p>Accounts. [NIST AC-2]</p>"),
        FakePage("Access Control Standard", "<p>Quarterly. [NIST AC-2] [NIST AC-6(5)]</p>"),
        FakePage("Access Review Procedure", "<p>Steps. [NIST AC-2]</p>"),
        FakePage("Backup and Restore Standard", "<p>Drills. [NIST CP-9]</p>"),
        FakePage("Laptop Encryption", "<p>FileVault.</p>"),
        FakePage("On-call Rotation", "<p>PagerDuty.</p>"),
    ]


# --------------------------------------------------------------------------
# Reading a title
# --------------------------------------------------------------------------


def test_a_tier_word_is_stripped_to_find_the_subject():
    assert split_title("Access Control Standard") == ("Access Control", "standard")
    assert split_title("Access Review Procedure") == ("Access Review", "procedure")
    assert split_title("Incident Response Policy") == ("Incident Response", "policy")


def test_synonyms_for_a_tier_are_recognised():
    assert split_title("Key Rotation Runbook")[1] == "procedure"
    assert split_title("Logging SOP")[1] == "procedure"


def test_a_title_with_no_tier_word_yields_no_tier():
    """That is what marks a page the naming convention did not reach."""
    assert split_title("Laptop Encryption") == ("Laptop Encryption", "")


def test_noise_words_do_not_become_the_topic_name():
    assert split_title("Standard for the Access Review v2")[0] == "Access Review"


# --------------------------------------------------------------------------
# Grouping by what the titles already say
# --------------------------------------------------------------------------


def test_pages_sharing_a_title_stem_become_one_topic():
    topics, leftovers = group_by_convention(_space())

    access = next(t for t in topics if t.name == "Access Control")
    assert sorted(tier for tier, _ in access.pages) == ["policy", "standard"]
    assert "title stem" in access.evidence
    assert {p.title for p in leftovers} == {"Laptop Encryption", "On-call Rotation"}


def test_anchor_controls_come_from_what_the_pages_cite():
    topics, _ = group_by_convention(_space())

    access = next(t for t in topics if t.name == "Access Control")
    assert "AC-2" in access.nist_controls


def test_an_enhancement_collapses_into_the_control_it_belongs_to():
    """The registry anchors controls and inherits enhancements, so proposing
    AC-6(5) beside AC-6 would be noise."""
    topics, _ = group_by_convention(_space())

    access = next(t for t in topics if t.name == "Access Control")
    assert "AC-6" in access.nist_controls
    assert not any("(" in control for control in access.nist_controls)


def test_the_most_cited_control_is_proposed_first():
    topics, _ = group_by_convention(_space())

    access = next(t for t in topics if t.name == "Access Control")
    assert access.nist_controls[0] == "AC-2"


# --------------------------------------------------------------------------
# The residue
# --------------------------------------------------------------------------


def test_without_a_model_unconventional_pages_are_listed_not_forced():
    """A wrong grouping is harder to spot than an absent one."""
    report = discover_topics(_space(), space="SEC", provider=None)

    assert report.unplaced == ["Laptop Encryption", "On-call Rotation"]
    assert all("model" not in t.evidence for t in report.topics)


def test_a_model_groups_only_what_the_conventions_missed():
    provider = ScriptedProvider("Endpoint Security: Laptop Encryption")

    report = discover_topics(_space(), space="SEC", provider=provider)

    assert provider.calls == 1
    endpoint = next(t for t in report.topics if t.name == "Endpoint Security")
    assert [title for _, title in endpoint.pages] == ["Laptop Encryption"]
    assert report.unplaced == ["On-call Rotation"], "what it left out stays unplaced"


def test_a_title_the_model_invented_is_dropped():
    """The proposal turns into page lookups, and a title that does not exist
    becomes a skip nobody can explain later."""
    provider = ScriptedProvider("Made Up: A Page That Does Not Exist | Laptop Encryption")

    report = discover_topics(_space(), space="SEC", provider=provider)

    made_up = next(t for t in report.topics if t.name == "Made Up")
    assert [title for _, title in made_up.pages] == ["Laptop Encryption"]


def test_a_page_is_never_placed_in_two_topics():
    provider = ScriptedProvider("One: Laptop Encryption\nTwo: Laptop Encryption")

    report = discover_topics(_space(), space="SEC", provider=provider)

    placed = [title for topic in report.topics for _, title in topic.pages]
    assert placed.count("Laptop Encryption") == 1


def test_unparseable_model_output_leaves_everything_unplaced():
    report = discover_topics(_space(), space="SEC", provider=ScriptedProvider("I'm sorry Dave"))

    assert "Laptop Encryption" in report.unplaced


# --------------------------------------------------------------------------
# The proposal
# --------------------------------------------------------------------------


def test_every_owner_is_unassigned():
    report = discover_topics(_space(), space="SEC", provider=None)

    assert report.topics
    assert all(topic.owner == UNASSIGNED for topic in report.topics)


def test_the_rendered_registry_is_loadable_yaml_with_no_owners_filled_in():
    from policyforge.topics.registry import parse_topics

    report = discover_topics(_space(), space="SEC", provider=None)
    text = render_registry(report)
    parsed = parse_topics(yaml.safe_load(text))

    assert parsed, "the proposal parses as a registry"
    assert all(topic.owner == UNASSIGNED for topic in parsed)
    access = next(t for t in parsed if t.name == "Access Control")
    assert access.confluence["space"] == "SEC"
    assert access.confluence["pages"]["standard"] == "Access Control Standard"


def test_the_file_says_it_is_not_usable_as_is():
    """It has to survive a skim and stop anyone running from it unedited."""
    text = render_registry(discover_topics(_space(), space="SEC", provider=None))

    assert "Not usable as-is" in text
    assert UNASSIGNED in text
    assert "coverage" in text


def test_the_report_explains_why_owners_are_blank():
    report = discover_topics(_space(), space="SEC", provider=None)

    assert "worse than a blank one" in report.format_report()
    assert "Proposed" in report.format_report()


def test_the_discover_command_writes_a_proposal(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda: {"zardoz": {"host": "https://x"}})
    monkeypatch.setattr(
        "policyforge.export.confluence_search.search_pages", lambda **kwargs: _space()
    )
    out = tmp_path / "topics.proposed.yaml"

    result = CliRunner().invoke(
        cli_mod.cli,
        ["zardoz", "discover", "--space", "SEC", "--out", str(out), "--no-llm"],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert UNASSIGNED in out.read_text(encoding="utf-8")
    assert "Access Control" in result.output

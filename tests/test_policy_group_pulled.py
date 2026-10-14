"""`generate --policy` finds a topic's Standard by the rule `edit-topic` does
(22, part 2b; ba on PR 445): a file bound to the page the registry declares for
the topic, as `pull` writes it, is that topic's Standard even with no
`topic:` in its frontmatter.

Hand-written: the registry, the pulled file and what it should be called.
"""

from __future__ import annotations

from click.testing import CliRunner

import policyforge.cli as cli_mod
from tests.test_policy_group import GOOD_DRAFT, _Fake, _project, _run

REGISTRY_WITH_PAGES = """
policies:
  - {name: Access Control, owner: CISO}
topics:
  - name: Identity Lifecycle
    owner: IAM
    policy: Access Control
    nist_controls: [AC-2]
    confluence: {space: SEC, pages: {standard: Identity Lifecycle Standard Page}}
  - name: Authentication
    owner: Security
    policy: Access Control
    nist_controls: [IA-5]
    confluence: {space: SEC, pages: {standard: Authentication Standard Page}}
"""


def _pulled(tmp_path):
    """Authentication's Standard as `pull` writes it: bound to the page, no
    `topic:` key."""
    path = tmp_path / "docs" / "standards" / "authentication.md"
    path.write_text(
        "---\ntitle: Authentication Standard\ntier: standard\nowner: Security\n"
        "confluence:\n  space: SEC\n  title: Authentication Standard Page\n"
        "---\n\n# Authentication Standard\n\nRequirements.\n",
        encoding="utf-8",
    )


def _registry(tmp_path, text=REGISTRY_WITH_PAGES):
    (tmp_path / "config" / "topics.yaml").write_text(text, encoding="utf-8")


def test_a_standard_pull_wrote_is_found_by_the_page_it_is_bound_to(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch)
    _registry(tmp_path)
    _pulled(tmp_path)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    assert "Generate them first" not in result.output
    text = (tmp_path / "docs" / "policies" / "access-control.md").read_text(encoding="utf-8")
    assert "- [Authentication Standard](../standards/authentication.md) — Security" in text
    assert fake.prompts and "Authentication Standard" in fake.prompts[0]


def test_the_same_pulled_standard_is_what_edit_topic_would_find(tmp_path, monkeypatch):
    """One rule: where `generate --policy` calls a topic's Standard missing,
    `edit-topic` must not find one, and the reverse."""
    from policyforge.edit.tree import select_topic_files
    from policyforge.topics.registry import load_registry

    _project(tmp_path, monkeypatch)
    _registry(tmp_path)
    _pulled(tmp_path)
    registry = load_registry(tmp_path / "config" / "topics.yaml")
    topic = next(t for t in registry.topics if t.name == "Authentication")

    selection = select_topic_files(tmp_path / "docs", topic, tiers={"standard"})

    assert selection.files["standard"].relative == "standards/authentication.md"
    assert "bound to page" in selection.files["standard"].matched_by


def test_a_pulled_file_and_a_topic_file_for_one_topic_are_refused_as_two(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _registry(tmp_path)
    path = tmp_path / "docs" / "standards" / "authentication-pulled.md"
    path.write_text(
        "---\ntitle: Authentication Standard (pulled)\ntier: standard\n"
        "confluence:\n  space: SEC\n  title: Authentication Standard Page\n"
        "---\n\n# Authentication Standard (pulled)\n\nText.\n",
        encoding="utf-8",
    )

    def refuse(_cfg):
        raise AssertionError("a provider was built for a refused run")

    monkeypatch.setattr(cli_mod, "get_provider", refuse)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 2
    assert "has more than one Standard" in result.output
    assert "standards/authentication-pulled.md" in result.output
    assert "standards/authentication.md" in result.output


def test_a_standard_bound_to_another_topics_page_is_not_this_topics(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, standards=False)
    _registry(tmp_path)
    directory = tmp_path / "docs" / "standards"
    directory.mkdir(parents=True)
    (directory / "other.md").write_text(
        "---\ntitle: Other Standard\ntier: standard\nconfluence:\n  space: SEC\n"
        "  title: Some Other Page\n---\n\n# Other Standard\n\nText.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda _cfg: _Fake(GOOD_DRAFT))

    result = CliRunner().invoke(
        cli_mod.cli, ["generate", "--tier", "policy", "--policy", "Access Control"]
    )

    assert result.exit_code == 2
    assert "Identity Lifecycle" in result.output and "Authentication" in result.output

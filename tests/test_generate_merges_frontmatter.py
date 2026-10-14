"""`generate` merges frontmatter the way `pull` does (#404).

It used to build a document's frontmatter from scratch, so a hand-set `owner:`,
a key a person added, and the page version `pull` records inside `confluence:`
were replaced silently on the next run. The body hash of the edit guard covers
the body only. 80's ruling: `generate` updates the keys it owns (the stamp,
`topic`, `tier`) and keeps every other, with a hand-set owner kept over the
synthesis's and one line naming both.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
from click.testing import CliRunner

SYNTHESIS = """---
topic: Access Control
owner: Security Operations
---

- Staff must review access quarterly. [NIST 800-53 AC-2]
"""

DRAFT = "# Access Control Standard\n\nStaff must review access quarterly.\n"
REDRAFT = "# Access Control Standard\n\nStaff must review access every quarter.\n"

TOPICS_YAML = """topics:
  - name: Access Control
    owner: Security Operations
    confluence:
      space: SEC
      pages:
        standard: Access Control Standard
"""


class _Provider:
    def __init__(self, text: str):
        self.text = text

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=self.text, model="fake-writer")

    def check(self):
        return True


class _Project:
    def __init__(self, tmp_path: Path, monkeypatch):
        import policyforge.cli as cli_mod
        from policyforge.llm.ledger import wrap

        self.root = tmp_path
        self.provider = _Provider(DRAFT)
        self.cli_mod = cli_mod
        monkeypatch.chdir(tmp_path)
        (tmp_path / "access-control.md").write_text(SYNTHESIS, encoding="utf-8")
        config = {"org": {"name": "Acme", "industry": "Health"}, "llm": {"provider": "local"}}
        monkeypatch.setattr(cli_mod, "load_config", lambda: config)
        monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: wrap(self.provider, cfg))

    def generate(self, *extra: str, text: str | None = None):
        if text is not None:
            self.provider.text = text
        args = [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(self.root / "access-control.md"),
            "--history-dir",
            str(self.root / "history"),
            *extra,
        ]
        return CliRunner().invoke(self.cli_mod.cli, args)

    @property
    def document(self) -> Path:
        return self.root / "docs" / "standards" / "access-control.md"

    def metadata(self) -> dict:
        return frontmatter.loads(self.document.read_text(encoding="utf-8")).metadata

    def set_metadata(self, **keys) -> None:
        """What a person (or `pull`) does: change the frontmatter, not the body."""
        post = frontmatter.loads(self.document.read_text(encoding="utf-8"))
        post.metadata.update(keys)
        self.document.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def test_a_hand_set_owner_survives_regeneration_and_the_line_names_both(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    assert p.generate().exit_code == 0
    assert p.metadata()["owner"] == "Security Operations"
    p.set_metadata(owner="Platform Team")

    result = p.generate(text=REDRAFT)

    assert result.exit_code == 0, result.output
    assert p.metadata()["owner"] == "Platform Team"
    assert "'Platform Team'" in result.output and "'Security Operations'" in result.output
    assert "Kept the owner" in result.output


def test_an_owner_that_agrees_with_the_synthesis_prints_nothing(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    assert p.generate().exit_code == 0
    result = p.generate(text=REDRAFT)
    assert "Kept the owner" not in result.output
    assert p.metadata()["owner"] == "Security Operations"


def test_a_document_with_no_owner_takes_the_syntheses(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    assert p.generate().exit_code == 0
    post = frontmatter.loads(p.document.read_text(encoding="utf-8"))
    del post.metadata["owner"]
    p.document.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")

    assert p.generate(text=REDRAFT).exit_code == 0
    assert p.metadata()["owner"] == "Security Operations"


def test_the_page_version_pull_recorded_survives(tmp_path, monkeypatch):
    """`pull` records the page's version inside `confluence:`, with the
    binding. Regenerating must keep it, and take only what the block lacks
    from the registry."""
    p = _Project(tmp_path, monkeypatch)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    assert p.generate().exit_code == 0
    p.set_metadata(confluence={"space": "SEC", "title": "Access Control Standard", "version": 7})

    assert p.generate(text=REDRAFT).exit_code == 0

    assert p.metadata()["confluence"] == {
        "space": "SEC",
        "title": "Access Control Standard",
        "version": 7,
    }


def test_a_binding_the_document_has_is_not_overwritten_by_the_registry(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    assert p.generate().exit_code == 0
    p.set_metadata(confluence={"space": "OPS", "title": "Our Own Page Title"})

    assert p.generate(text=REDRAFT).exit_code == 0

    assert p.metadata()["confluence"] == {"space": "OPS", "title": "Our Own Page Title"}


def test_a_key_somebody_added_is_kept(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    assert p.generate().exit_code == 0
    p.set_metadata(reviewed_by="J. Okafor", content_class="licensed")

    assert p.generate(text=REDRAFT).exit_code == 0

    assert p.metadata()["reviewed_by"] == "J. Okafor"
    assert p.metadata()["content_class"] == "licensed"


def test_the_keys_generate_owns_are_updated(tmp_path, monkeypatch):
    """The stamp follows the new body (so the next run can tell an edit), and
    topic and tier come from the synthesis and the command line."""
    p = _Project(tmp_path, monkeypatch)
    assert p.generate().exit_code == 0
    first = p.metadata()["generated_by"]["body_sha256"]
    p.set_metadata(topic="Something Else", tier="policy")

    assert p.generate(text=REDRAFT).exit_code == 0

    meta = p.metadata()
    assert meta["generated_by"]["body_sha256"] != first
    assert meta["generated_by"]["models"] == ["fake-writer"]
    assert meta["topic"] == "Access Control"
    assert meta["tier"] == "standard"


def test_a_new_document_is_written_as_before(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    result = p.generate()
    assert result.exit_code == 0, result.output
    meta = p.metadata()
    assert meta["owner"] == "Security Operations" and meta["topic"] == "Access Control"
    assert meta["tier"] == "standard" and "generated_by" in meta
    assert "Kept the owner" not in result.output


def test_a_synthesis_with_no_topic_leaves_the_documents_own(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    assert p.generate().exit_code == 0
    (tmp_path / "access-control.md").write_text(
        "---\nowner: Security Operations\n---\n\n- Staff must review access quarterly.\n",
        encoding="utf-8",
    )

    assert p.generate(text=REDRAFT).exit_code == 0

    assert p.metadata()["topic"] == "Access Control"


# ---- what 9b and 1d found on PR 407 ------------------------------------------------------


TWO_TOPICS_YAML = """topics:
  - name: Access Control
    owner: Security Operations
    confluence:
      space: SEC
      pages:
        standard: Access Control Standard
  - name: Incident Response
    owner: Response Team
    confluence:
      space: SEC
      pages:
        standard: Incident Response Standard
"""


def test_a_document_of_another_topic_does_not_carry_that_topics_page(tmp_path, monkeypatch):
    """9b's case: the target holds an unedited Access Control document with
    its binding, page id and pulled version, and the synthesis is Incident
    Response. Carried across, `publish` would write one topic's text over the
    other's page."""
    p = _Project(tmp_path, monkeypatch)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "topics.yaml").write_text(TWO_TOPICS_YAML, encoding="utf-8")
    assert p.generate().exit_code == 0
    p.set_metadata(
        owner="IAM Engineering",
        confluence={"space": "SEC", "title": "Old Page", "page_id": 1234, "version": 7},
        reviewed_by="J. Okafor",
    )
    (tmp_path / "access-control.md").write_text(
        SYNTHESIS.replace("Access Control", "Incident Response").replace(
            "Security Operations", "Response Team"
        ),
        encoding="utf-8",
    )

    result = p.generate(text=REDRAFT)

    assert result.exit_code == 0, result.output
    meta = p.metadata()
    assert meta["topic"] == "Incident Response"
    assert meta["confluence"] == {"space": "SEC", "title": "Incident Response Standard"}
    assert meta["owner"] == "Response Team"
    assert meta["reviewed_by"] == "J. Okafor", "what belongs to no one topic is still kept"
    assert "'Access Control'" in result.output and "'Incident Response'" in result.output
    assert "confluence" in result.output and "not carried over" in result.output


def test_the_same_topic_spelled_in_another_case_is_the_same_document(tmp_path, monkeypatch):
    p = _Project(tmp_path, monkeypatch)
    assert p.generate().exit_code == 0
    p.set_metadata(topic="access control", owner="IAM Engineering")

    result = p.generate(text=REDRAFT)

    assert p.metadata()["owner"] == "IAM Engineering"
    assert "not carried over" not in result.output


def test_a_binding_spelled_under_targets_is_filled_there_and_not_doubled(tmp_path, monkeypatch):
    """1d's case: `pull` updates `targets: {confluence: ...}` in place. A
    second top-level block from the registry is an error `check` reports."""
    from policyforge.content.check import check_tree

    p = _Project(tmp_path, monkeypatch)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    assert p.generate().exit_code == 0
    post = frontmatter.loads(p.document.read_text(encoding="utf-8"))
    post.metadata.pop("confluence", None)
    post.metadata["targets"] = {"confluence": {"space": "SEC", "version": 7}}
    p.document.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")

    assert p.generate(text=REDRAFT).exit_code == 0

    meta = p.metadata()
    assert "confluence" not in meta
    assert meta["targets"]["confluence"] == {
        "space": "SEC",
        "title": "Access Control Standard",
        "version": 7,
    }
    report = check_tree(tmp_path / "docs")
    assert not [f for f in report.findings if "different contents" in f.message]

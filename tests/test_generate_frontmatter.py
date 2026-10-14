"""`generate` writes topic, owner and tier frontmatter (#384).

A generated document says what it is: `topic`, `owner` and `tier` are
carried from the synthesis and CLI arguments into the document's
frontmatter, merged with the provenance stamp. When the topic registry
declares a Confluence page for that tier, `generate` writes the
`confluence:` binding too. Values absent from the synthesis are left out,
never guessed, and `generate` names what is missing.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest
from click.testing import CliRunner

SYNTHESIS_FULL = """---
topic: Access Control
owner: Security Operations
---

- Staff must review access quarterly. [NIST 800-53 AC-2]
"""

SYNTHESIS_NO_OWNER = """---
topic: Access Control
---

- Staff must review access quarterly. [NIST 800-53 AC-2]
"""

SYNTHESIS_NO_TOPIC = """---
owner: Security Operations
---

- Staff must review access quarterly. [NIST 800-53 AC-2]
"""

STANDARD_TEXT = """# Access Control Standard

Staff must review access quarterly. [NIST 800-53 AC-2]
"""

TOPICS_YAML = """topics:
  - name: Access Control
    owner: Security Operations
    confluence:
      space: SEC
      pages:
        standard: Access Control Standard
        policy: Access Control Policy
        procedure: Access Review Procedure
"""


class _FakeProvider:
    def __init__(self, text: str = STANDARD_TEXT):
        self.text = text
        self.calls: list[dict] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(text=self.text, model="fake-writer")

    def check(self):
        return True


def _run_generate(
    tmp_path: Path,
    monkeypatch,
    *,
    tier: str = "standard",
    synthesis_text: str = SYNTHESIS_FULL,
    standard_text: str = STANDARD_TEXT,
    topics_yaml: str | None = None,
    provider: _FakeProvider | None = None,
):
    import policyforge.cli as cli_mod

    provider = provider or _FakeProvider()
    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(synthesis_text, encoding="utf-8")

    config = {
        "org": {"name": "Acme", "industry": "Health"},
        "llm": {"provider": "local"},
    }
    from policyforge.llm.ledger import wrap

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: wrap(provider, cfg))

    if topics_yaml is not None:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "topics.yaml").write_text(topics_yaml, encoding="utf-8")

    out_path = tmp_path / "out" / f"access-control-{tier}.md"
    args = [
        "generate",
        "--tier",
        tier,
        "--synthesis",
        str(synthesis),
        "--out",
        str(out_path),
        "--history-dir",
        str(tmp_path / "history"),
    ]

    if tier in ("policy", "procedure"):
        std_file = tmp_path / "standard.md"
        std_file.write_text(standard_text, encoding="utf-8")
        args.extend(["--standard", str(std_file)])

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, args)
    return result, out_path


@pytest.mark.parametrize("tier", ["standard", "policy", "procedure"])
def test_generate_writes_topic_owner_and_tier_frontmatter(tmp_path, monkeypatch, tier):
    """A fake-provider generate for each tier writes topic, owner and tier."""
    result, out_path = _run_generate(tmp_path, monkeypatch, tier=tier)
    assert result.exit_code == 0, result.output
    assert out_path.exists()

    doc = frontmatter.loads(out_path.read_text(encoding="utf-8"))
    assert doc.metadata.get("tier") == tier
    assert doc.metadata.get("topic") == "Access Control"
    assert doc.metadata.get("owner") == "Security Operations"
    # Merged with the generated_by provenance stamp
    assert "generated_by" in doc.metadata
    assert doc.metadata["generated_by"].get("models") == ["fake-writer"]


# The titles are written out by hand from TOPICS_YAML, not looked up from the
# registry: a lookup would agree with whichever page the code chose. The
# procedure's page is not named like the others, so a title built from the
# tier's name is wrong here too.
@pytest.mark.parametrize(
    ("tier", "title"),
    [
        ("standard", "Access Control Standard"),
        ("policy", "Access Control Policy"),
        ("procedure", "Access Review Procedure"),
    ],
)
def test_registry_with_page_binding_writes_confluence_frontmatter(
    tmp_path, monkeypatch, tier, title
):
    """A registry with a page binding writes confluence: {space, title}, and
    the title is the page of the tier being generated: a Policy bound to the
    Standard's page would be published over the Standard."""
    result, out_path = _run_generate(tmp_path, monkeypatch, tier=tier, topics_yaml=TOPICS_YAML)
    assert result.exit_code == 0, result.output
    assert out_path.exists()

    doc = frontmatter.loads(out_path.read_text(encoding="utf-8"))
    assert doc.metadata.get("confluence") == {"space": "SEC", "title": title}


def test_the_topic_is_matched_to_the_registry_without_regard_to_case(tmp_path, monkeypatch):
    """A synthesis's `access control` finds the registry's `Access Control`:
    the match is case-insensitive on purpose, so a differently cased topic
    still gets its page binding rather than none."""
    synthesis = SYNTHESIS_FULL.replace("topic: Access Control", "topic: access control")
    assert "topic: access control" in synthesis
    result, out_path = _run_generate(
        tmp_path, monkeypatch, tier="policy", synthesis_text=synthesis, topics_yaml=TOPICS_YAML
    )
    assert result.exit_code == 0, result.output

    doc = frontmatter.loads(out_path.read_text(encoding="utf-8"))
    assert doc.metadata.get("confluence") == {"space": "SEC", "title": "Access Control Policy"}


def test_synthesis_with_no_owner_writes_no_owner_and_prints_line(tmp_path, monkeypatch):
    """A synthesis with no owner writes no owner key and prints the line."""
    result, out_path = _run_generate(tmp_path, monkeypatch, synthesis_text=SYNTHESIS_NO_OWNER)
    assert result.exit_code == 0, result.output
    assert out_path.exists()

    assert (
        "No owner in the synthesis frontmatter — the draft will use [Responsible Team]."
        in result.output
    )
    doc = frontmatter.loads(out_path.read_text(encoding="utf-8"))
    assert "owner" not in doc.metadata
    assert doc.metadata.get("topic") == "Access Control"
    assert doc.metadata.get("tier") == "standard"


def test_synthesis_with_no_topic_writes_no_topic_and_prints_line(tmp_path, monkeypatch):
    """A synthesis with no topic writes no topic key and prints the line."""
    result, out_path = _run_generate(tmp_path, monkeypatch, synthesis_text=SYNTHESIS_NO_TOPIC)
    assert result.exit_code == 0, result.output
    assert out_path.exists()

    assert "No topic in the synthesis frontmatter." in result.output
    doc = frontmatter.loads(out_path.read_text(encoding="utf-8"))
    assert "topic" not in doc.metadata
    assert doc.metadata.get("owner") == "Security Operations"
    assert doc.metadata.get("tier") == "standard"


def test_checker_reports_no_owner_when_owner_is_missing(tmp_path, monkeypatch):
    """The checker reports 'has no owner' when owner is missing from frontmatter."""
    from policyforge.content.check import check_tree

    docs_root = tmp_path / "docs"
    standards_dir = docs_root / "standards"
    standards_dir.mkdir(parents=True)

    result, out_path = _run_generate(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output

    doc_file = standards_dir / "access-control.md"
    doc_file.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")

    report = check_tree(docs_root)
    owner_findings = [f for f in report.findings if "has no owner" in f.message]
    assert not owner_findings, f"Unexpected owner findings: {owner_findings}"

    # Strip owner from the document frontmatter
    doc = frontmatter.loads(doc_file.read_text(encoding="utf-8"))
    doc.metadata.pop("owner", None)
    doc_file.write_text(frontmatter.dumps(doc) + "\n", encoding="utf-8")

    report_stripped = check_tree(docs_root)
    assert any("has no owner" in f.message for f in report_stripped.findings)


# ---- carried by b5 (#384): the two paths the first version didn't pin -------


def test_a_document_with_no_recorded_models_still_carries_its_frontmatter(tmp_path, monkeypatch):
    """Where the ledger saw no call (a provider built outside `get_provider`)
    there is no `generated_by` stamp, and absence honestly means "not known".
    The topic, owner and tier travel anyway: they come from the synthesis and
    the command line, not from the ledger. That branch writes through
    `mdformat` itself, so the file must be what the markdown gate accepts."""
    import policyforge.cli as cli_mod
    from policyforge.export.markdown_exporter import check_markdown_quality

    provider = _FakeProvider()
    # The helper writes the synthesis and patches config; its own run is wrapped.
    result, _wrapped = _run_generate(tmp_path, monkeypatch, provider=provider)
    assert result.exit_code == 0, result.output
    # Same command, with the provider handed back unwrapped (no ledger scope).
    monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: provider)
    bare = tmp_path / "bare" / "access-control-standard.md"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(tmp_path / "access-control.md"),
            "--out",
            str(bare),
            "--history-dir",
            str(tmp_path / "history2"),
        ],
    )

    assert result.exit_code == 0, result.output
    doc = frontmatter.loads(bare.read_text(encoding="utf-8"))
    # No model was recorded, so there is no attribution to make: the block
    # holds the body hash (#385) and nothing that reads as a model's claim.
    # The catalog keys are the input's, not a model's (443 part 2): this
    # synthesis recorded none, so it is named as not recorded.
    assert set(doc.metadata["generated_by"]) == {
        "body_sha256",
        "catalogs",
        "catalogs_not_recorded",
    }
    assert doc.metadata.get("tier") == "standard"
    assert doc.metadata.get("topic") == "Access Control"
    assert doc.metadata.get("owner") == "Security Operations"
    assert "Staff must review access quarterly." in doc.content
    assert check_markdown_quality(bare)


def test_an_unreadable_registry_says_so_and_writes_no_binding(tmp_path, monkeypatch):
    """The registry's own docstring calls a malformed file fatal. Here the
    draft is already paid for, so the document is written without the
    `confluence:` block, and the run says exactly that."""
    result, out_path = _run_generate(
        tmp_path,
        monkeypatch,
        topics_yaml="topics:\n  - name: Access Control\n    controls: [AC-2\n",
    )

    assert result.exit_code == 0, result.output
    assert "could not be read" in result.output
    assert "no confluence: binding was written" in result.output
    doc = frontmatter.loads(out_path.read_text(encoding="utf-8"))
    assert "confluence" not in doc.metadata
    assert doc.metadata.get("topic") == "Access Control"

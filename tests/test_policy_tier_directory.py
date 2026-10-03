"""Where `generate` writes a Policy, and whether `check` reads it there (#411).

`generate` built its directory as `f"{tier}s"`, so a Policy went to
`output/policys/`, which `check` does not recognise as a tier: every
generated Policy got "has no tier". The writers and the reader now share one
table (`content.tree.TIER_DIRS` / `TIER_DIRECTORY`).
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from policyforge.content.check import check_tree
from policyforge.content.tree import TIER_DIRECTORY, load_content_tree


class _PolicyWriter:
    """A model that writes a Policy: principles, and no control citations,
    as the README says a Policy does."""

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(
            text="# Access Review Policy\n\nThe organization reviews who has access.\n",
            model="fake",
            stop_reason="stop",
        )

    def check(self):
        return True


def _generate_policy(tmp_path, monkeypatch) -> Path:
    import policyforge.cli as cli_mod

    monkeypatch.chdir(tmp_path)
    synthesis = tmp_path / "output" / "synthesis" / "access-review.md"
    synthesis.parent.mkdir(parents=True)
    synthesis.write_text("- Access must be reviewed quarterly. [NIST AC-2]\n", encoding="utf-8")
    standard = tmp_path / "published" / "access-review-standard.md"
    standard.parent.mkdir()
    standard.write_text("# Access Review Standard\n\nReview access. [NIST AC-2]\n", "utf-8")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"org": {"name": "Acme"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: _PolicyWriter())

    # No --out: the default directory is what #411 is about.
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate", "--tier", "policy", "--synthesis", str(synthesis),
            "--standard", str(standard), "--history-dir", str(tmp_path / "history"),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    return tmp_path / "output"


def test_a_generated_policy_lands_where_check_reads_a_policy(tmp_path, monkeypatch):
    output = _generate_policy(tmp_path, monkeypatch)
    assert (output / "policies" / "access-review.md").is_file()
    assert not (output / "policys").exists()
    documents, _ = load_content_tree(output)
    (policy,) = [d for d in documents if d.relative_path.endswith("access-review.md")]
    assert policy.tier == "policy"
    report = check_tree(output, synthesis_dir=output / "synthesis")
    about = [f.message for f in report.findings if f.path == policy.relative_path]
    assert not [m for m in about if "tier" in m], about
    # A Policy drops citations by design, so none is "missing" (#411). This
    # was still reported after the directory was fixed: the citation check
    # did not read the tier at all.
    assert not [m for m in about if "citation" in m], about


def test_a_standard_still_loses_nothing_its_synthesis_carries(tmp_path):
    """The Policy exemption is by tier: a Standard missing a citation is
    still reported."""
    root = tmp_path / "output"
    (root / "synthesis").mkdir(parents=True)
    (root / "synthesis" / "access-review.md").write_text("- Review. [NIST AC-2]\n", "utf-8")
    (root / "standards").mkdir()
    (root / "standards" / "access-review.md").write_text("# Access Review\n\nReview.\n", "utf-8")
    report = check_tree(root, synthesis_dir=root / "synthesis")
    assert any("missing 1 citation" in f.message for f in report.findings), report.findings


def test_a_document_under_policys_is_told_where_to_move(tmp_path):
    """An `output/` tree generated before #411 is named, not passed over."""
    root = tmp_path / "output"
    (root / "policys").mkdir(parents=True)
    (root / "policys" / "access-review.md").write_text("# Access Review\n\nText.\n", "utf-8")
    messages = [f.message for f in check_tree(root).findings]
    assert any("under policys/" in m and "move it to policies/" in m for m in messages), messages
    assert not any("has no tier" in m for m in messages), messages


def test_every_tier_has_the_directory_check_reads_it_from():
    """Derived from the reader's table, not from the writers: an edit that
    gives a writer its own spelling again cannot also change this."""
    from policyforge.content.tree import TIER_DIRS

    assert {TIER_DIRS[d]: d for d in TIER_DIRS} == TIER_DIRECTORY
    assert TIER_DIRECTORY["policy"] == "policies"

"""`generate` does not replace a document someone has edited (#385).

`generate` writes into the content tree now: the tree people edit, and the one
`pull` writes a wiki's edits into. 80's ruling on PR 402: it records a hash of
the body it wrote in the document's `generated_by` stamp, and refuses to
overwrite a document whose body no longer matches (or that has no stamp), in
one line, before any model is called. `--overwrite-edits` writes anyway, after
recording the replaced file in history. "No content change ... history
unchanged" is never printed over a file that was replaced.
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

FIRST_DRAFT = "# Access Control Standard\n\nStaff must review access quarterly.\n"
SECOND_DRAFT = "# Access Control Standard\n\nStaff must review access every quarter.\n"
EDIT = "HAND EDIT kept after review."

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
        self.calls = 0

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls += 1
        return LLMResponse(text=self.text, model="fake-writer")

    def check(self):
        return True


class _Session:
    """One project directory, and `generate` run in it as often as a test likes."""

    def __init__(self, tmp_path: Path, monkeypatch):
        import policyforge.cli as cli_mod
        from policyforge.llm.ledger import wrap

        self.root = tmp_path
        self.provider = _Provider(FIRST_DRAFT)
        self.history = tmp_path / "history"
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
            str(self.history),
            *extra,
        ]
        return CliRunner().invoke(self.cli_mod.cli, args)

    @property
    def document(self) -> Path:
        """Where `generate` writes with no --out: the content tree's default."""
        return self.root / "docs" / "standards" / "access-control.md"

    def versions(self) -> list[str]:
        folder = self.history / "standard" / "access-control"
        return [p.read_text(encoding="utf-8") for p in sorted(folder.glob("v*.md"))]


def _edit(path: Path) -> str:
    """A person's edit: a reviewed section appended to the body."""
    path.write_text(path.read_text(encoding="utf-8") + "\n" + EDIT + "\n", encoding="utf-8")
    return path.read_text(encoding="utf-8")


def test_an_unedited_document_regenerates_freely(tmp_path, monkeypatch):
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0
    again = s.generate(text=SECOND_DRAFT)
    assert again.exit_code == 0, again.output
    assert "every quarter" in s.document.read_text(encoding="utf-8")
    assert s.provider.calls == 2


def test_an_edited_document_is_refused_and_left_as_it_was(tmp_path, monkeypatch):
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0
    edited = _edit(s.document)
    before = s.provider.calls

    result = s.generate(text=SECOND_DRAFT)

    assert result.exit_code == 2, result.output
    assert str(Path("docs") / "standards" / "access-control.md") in result.output
    assert "was edited since it was generated" in result.output
    assert "--overwrite-edits" in result.output
    assert s.document.read_text(encoding="utf-8") == edited
    # Refused before the model was asked: a refusal never costs a draft.
    assert s.provider.calls == before


def test_overwrite_edits_replaces_the_file_and_keeps_the_old_text_in_history(tmp_path, monkeypatch):
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0
    _edit(s.document)

    result = s.generate("--overwrite-edits", text=SECOND_DRAFT)

    assert result.exit_code == 0, result.output
    now = s.document.read_text(encoding="utf-8")
    assert EDIT not in now
    assert "every quarter" in now
    kept = [v for v in s.versions() if EDIT in v]
    assert len(kept) == 1, "the replaced file is recorded in history"
    assert "Recorded the replaced" in result.output


def test_a_file_with_no_stamp_is_treated_as_edited(tmp_path, monkeypatch):
    s = _Session(tmp_path, monkeypatch)
    s.document.parent.mkdir(parents=True)
    s.document.write_text("# Access Control Standard\n\nWritten by hand.\n", encoding="utf-8")

    refused = s.generate()
    assert refused.exit_code == 2, refused.output
    assert "was not written by `generate`" in refused.output
    assert s.document.read_text(encoding="utf-8").endswith("Written by hand.\n")
    assert s.provider.calls == 0

    forced = s.generate("--overwrite-edits")
    assert forced.exit_code == 0, forced.output
    assert any("Written by hand." in v for v in s.versions())


def test_a_document_stamped_before_the_hash_existed_is_refused(tmp_path, monkeypatch):
    """A stamp with no `body_sha256` can't show the body is untouched."""
    s = _Session(tmp_path, monkeypatch)
    s.document.parent.mkdir(parents=True)
    old = frontmatter.Post(
        "# Access Control Standard\n\nOld draft.\n", generated_by={"models": ["m"]}
    )
    s.document.write_text(frontmatter.dumps(old) + "\n", encoding="utf-8")

    result = s.generate()

    assert result.exit_code == 2, result.output
    assert "before `generate` recorded a hash" in result.output
    assert s.provider.calls == 0


def test_an_editors_trailing_newline_is_not_an_edit(tmp_path, monkeypatch):
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0
    s.document.write_text(s.document.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert s.generate(text=SECOND_DRAFT).exit_code == 0


def test_a_pulled_edit_is_an_edit(tmp_path, monkeypatch):
    """`pull` writes the wiki's body into this same file, under the old stamp."""
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0
    post = frontmatter.loads(s.document.read_text(encoding="utf-8"))
    post.content = "# Access Control Standard\n\nThe wiki's wording.\n"
    s.document.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")

    result = s.generate(text=SECOND_DRAFT)

    assert result.exit_code == 2, result.output
    assert "The wiki's wording." in s.document.read_text(encoding="utf-8")


def test_history_unchanged_is_not_said_over_a_file_that_was_rewritten(tmp_path, monkeypatch):
    """Same drafted text, but the file's frontmatter changed (a registry page
    binding appeared): the history line is true, so it says what happened to
    the file as well rather than "no change"."""
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "topics.yaml").write_text(TOPICS_YAML, encoding="utf-8")
    result = s.generate()

    assert result.exit_code == 0, result.output
    assert "No content change since" not in result.output
    assert "was rewritten" in result.output
    assert frontmatter.loads(s.document.read_text(encoding="utf-8")).metadata["confluence"] == {
        "space": "SEC",
        "title": "Access Control Standard",
    }


def test_the_history_line_is_still_said_when_the_file_did_not_change(tmp_path, monkeypatch):
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0
    result = s.generate()
    assert result.exit_code == 0, result.output
    assert "No content change since the last recorded version" in result.output


def test_a_checkout_with_crlf_line_endings_is_not_an_edit(tmp_path, monkeypatch):
    """git's autocrlf, or an editor, rewrites every line ending; the text is
    the same, so the body hash is too."""
    s = _Session(tmp_path, monkeypatch)
    assert s.generate().exit_code == 0
    text = s.document.read_text(encoding="utf-8")
    with s.document.open("w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(text)
    assert b"\r\n" in s.document.read_bytes()
    assert s.generate(text=SECOND_DRAFT).exit_code == 0


def test_a_draft_the_markdown_formatter_rewrites_is_still_unedited(tmp_path, monkeypatch):
    """The hash is of the body as STORED, after mdformat has rewritten the
    model's `*` bullets: hashed before that, every untouched document would
    read as edited on the next run."""
    s = _Session(tmp_path, monkeypatch)
    bulleted = (
        "# Access Control Standard\n\n"
        "* Staff must review access quarterly.\n"
        "* Reviews are logged.\n"
    )
    assert s.generate(text=bulleted).exit_code == 0
    stored = s.document.read_text(encoding="utf-8")
    assert "- Staff must review access quarterly." in stored, "mdformat rewrote the bullets"

    again = s.generate(text=SECOND_DRAFT)

    assert again.exit_code == 0, again.output


def test_the_body_hash_is_the_same_for_lf_and_crlf_text():
    """`generate` reads the file as text (CRLF already folded), but the helper
    is also called with bytes-as-written, so it holds on its own."""
    from policyforge.content.provenance import body_digest, edit_state, stamp_with_body_hash

    lf = "---\ntier: standard\n---\n\n# Title\n\nline one\nline two\n"
    crlf = lf.replace("\n", "\r\n")
    assert body_digest(lf) == body_digest(crlf)

    stamped = stamp_with_body_hash(lf, {})
    assert edit_state(stamped) == "unedited"
    assert edit_state(stamped.replace("\n", "\r\n")) == "unedited"

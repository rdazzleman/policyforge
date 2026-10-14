"""Tests for one content tree: default generate to _content_dir (#385)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.llm.base import LLMResponse


class FakeProvider:
    def __init__(self, text: str = "# Doc\n\n..."):
        self.text = text
        self.calls: list[dict] = []

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 4096, temperature: float = 0.2
    ):
        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def test_default_content_dir_without_config(tmp_path, monkeypatch):
    """With no config, generate writes under docs/, and check and publish see it with no flag."""
    monkeypatch.chdir(tmp_path)
    synthesis_path = tmp_path / "media-handling.md"
    synthesis_path.write_text(
        "---\ntopic: Media Handling\nowner: IT Asset Management\n---\n\n"
        "- Media must be sanitized before disposal. [NIST MP-6]\n",
        encoding="utf-8",
    )
    config = {
        "org": {"name": "Acme", "industry": "H"},
        "zardoz": {"host": "https://wiki.example.com"},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda _cfg: FakeProvider("# Media Handling Standard\n\nMedia must be sanitized.\n"),
    )

    runner = CliRunner()
    gen_result = runner.invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis_path)],
    )
    assert gen_result.exit_code == 0, gen_result.output
    generated_file = tmp_path / "docs" / "standards" / "media-handling.md"
    assert generated_file.exists(), f"Expected {generated_file} to exist"
    assert not (tmp_path / "output" / "standards").exists()


def test_check_sees_it_with_no_flag(tmp_path, monkeypatch):
    """Check sees the generated document with no flag; restoring old default fails this."""
    monkeypatch.chdir(tmp_path)
    synthesis_path = tmp_path / "media-handling.md"
    synthesis_path.write_text(
        "---\ntopic: Media Handling\nowner: IT Asset Management\n---\n\n"
        "- Media must be sanitized before disposal. [NIST MP-6]\n",
        encoding="utf-8",
    )
    config = {
        "org": {"name": "Acme", "industry": "H"},
        "zardoz": {"host": "https://wiki.example.com"},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda _cfg: FakeProvider("# Media Handling Standard\n\nMedia must be sanitized.\n"),
    )

    runner = CliRunner()
    gen_result = runner.invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis_path)],
    )
    assert gen_result.exit_code == 0, gen_result.output

    # check sees the generated document with no --content-dir flag
    check_result = runner.invoke(cli_mod.cli, ["check"])
    assert check_result.exit_code == 0, check_result.output
    assert "Checked 1 document(s)" in check_result.output


def test_publish_sees_it_with_no_flag(tmp_path, monkeypatch):
    """Publish (plan only / dry run) sees the generated document with no flag."""
    monkeypatch.chdir(tmp_path)
    synthesis_path = tmp_path / "media-handling.md"
    synthesis_path.write_text(
        "---\ntopic: Media Handling\nowner: IT Asset Management\n---\n\n"
        "- Media must be sanitized before disposal. [NIST MP-6]\n",
        encoding="utf-8",
    )
    config = {
        "org": {"name": "Acme", "industry": "H"},
        "zardoz": {"host": "https://wiki.example.com"},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda _cfg: FakeProvider("# Media Handling Standard\n\nMedia must be sanitized.\n"),
    )

    runner = CliRunner()
    gen_result = runner.invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis_path)],
    )
    assert gen_result.exit_code == 0, gen_result.output

    # publish (plan only / dry run) sees the generated document with no --content-dir flag
    pub_result = runner.invoke(cli_mod.cli, ["publish"])
    assert pub_result.exit_code == 0, pub_result.output
    assert "1 document(s) declare no `confluence:` block" in pub_result.output


def test_configured_content_dir(tmp_path, monkeypatch):
    """With zardoz.content_dir set in config, generate, check and publish all use it."""
    monkeypatch.chdir(tmp_path)
    synthesis_path = tmp_path / "access-control.md"
    synthesis_path.write_text(
        "---\ntopic: Access Control\nowner: SecOps\n---\n\n- Passwords required. [NIST IA-5]\n",
        encoding="utf-8",
    )
    config = {
        "org": {"name": "Acme", "industry": "H"},
        "zardoz": {"content_dir": "custom_docs", "host": "https://wiki.example.com"},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda _cfg: FakeProvider("# Access Control Standard\n\nPasswords required.\n"),
    )

    runner = CliRunner()
    gen_result = runner.invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis_path)],
    )
    assert gen_result.exit_code == 0, gen_result.output
    generated_file = tmp_path / "custom_docs" / "standards" / "access-control.md"
    assert generated_file.exists(), f"Expected {generated_file} to exist"
    assert not (tmp_path / "docs").exists()

    # check sees the generated document under custom_docs with no flag
    check_result = runner.invoke(cli_mod.cli, ["check"])
    assert check_result.exit_code == 0, check_result.output
    assert "Checked 1 document(s)" in check_result.output

    # publish sees the generated document under custom_docs with no flag
    pub_result = runner.invoke(cli_mod.cli, ["publish"])
    assert pub_result.exit_code == 0, pub_result.output
    assert "1 document(s) declare no `confluence:` block" in pub_result.output


def test_zardoz_sync_uses_content_dir(tmp_path, monkeypatch):
    """zardoz sync resolves content_dir from _content_dir."""
    monkeypatch.chdir(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    synced_content_dirs: list[Path | None] = []

    class FakeReport:
        refused_empty = False

        def format_report(self):
            return "Synced ok"

    monkeypatch.setattr(
        "policyforge.zardoz.corpus.sync_corpus",
        lambda *a, content_dir=None, **kw: synced_content_dirs.append(content_dir) or FakeReport(),
    )
    monkeypatch.setattr("policyforge.topics.registry.load_topics", lambda _p: [])
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    runner = CliRunner()
    res = runner.invoke(cli_mod.cli, ["zardoz", "sync"])
    assert res.exit_code == 0, res.output
    assert synced_content_dirs[-1] == Path("docs")

    # With configured zardoz.content_dir
    custom_dir = tmp_path / "team_docs"
    custom_dir.mkdir()
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"zardoz": {"content_dir": "team_docs"}})
    res_custom = runner.invoke(cli_mod.cli, ["zardoz", "sync"])
    assert res_custom.exit_code == 0, res_custom.output
    assert synced_content_dirs[-1] == Path("team_docs")


# ---- carried by b5 (#385): what the first version did not pin ------------------


def _open_state(config: dict):
    """`open_session` as the MCP server and the shell call it: with a config
    dict it was handed, in a working directory that has no config file."""
    from policyforge.zardoz.startup import open_session

    class _Provider:
        def check(self):
            return True

    return open_session(
        topics_path=Path("topics.yaml"),
        corpus_dir=Path("corpus"),
        config=config,
        provider_factory=lambda _cfg: _Provider(),
        surface="mcp",
        plain=True,
    ).state


def test_a_session_uses_the_config_it_was_handed_for_the_content_tree(tmp_path, monkeypatch):
    """`open_session(config=...)` is also called with a dict the caller built
    (`mcp.server.build_state`). The content tree comes from THAT dict, not from
    whatever config file the working directory has: with a file-based lookup
    this state's content_dir is None, and the tree the caller named is never
    read."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "custom").mkdir()
    state = _open_state({"zardoz": {"content_dir": "custom"}})
    assert state.content_dir == Path("custom")


def test_a_session_with_no_content_dir_configured_takes_the_one_default(tmp_path, monkeypatch):
    """No `zardoz.content_dir` anywhere: the tree is `docs/`, when it is there."""
    monkeypatch.chdir(tmp_path)
    assert _open_state({}).content_dir is None
    (tmp_path / "docs").mkdir()
    assert _open_state({}).content_dir == Path("docs")


def test_a_configured_tree_that_is_missing_stays_the_configured_path(tmp_path, monkeypatch):
    """A typo'd `zardoz.content_dir` is still what the config says: the shell
    names it ("No content tree at custm") and does not answer from `docs/`.
    Only the DEFAULT is asked whether it is there (9b on PR 402)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    state = _open_state({"zardoz": {"content_dir": "custm"}})
    assert state.content_dir == Path("custm")


def test_the_shells_fallback_tree_is_the_default_generate_writes_to():
    """The skills read `state.content_dir or DEFAULT_CONTENT_DIR`; spelled out
    here by hand, so a default moved in one place and not the other is red."""
    from policyforge.zardoz.corpus import DEFAULT_CONTENT_DIR

    assert Path("docs") == DEFAULT_CONTENT_DIR


# `zardoz sync` with a Confluence host (80's ruling on #385): the corpus never
# widens silently. A host alone reads no local tree; a tree that is NAMED (the
# flag, or `zardoz.content_dir`) is read; with no host, `_content_dir` is the
# ruled default. Each case below is one clause.


def _fake_sync(monkeypatch, config: dict) -> list[Path | None]:
    """`zardoz sync` with the network and the corpus faked; returns the
    content_dir each run handed to `sync_corpus`."""
    from types import SimpleNamespace

    synced: list[Path | None] = []

    class _Report:
        refused_empty = False

        def format_report(self):
            return "Synced ok"

    monkeypatch.setattr(
        "policyforge.zardoz.corpus.sync_corpus",
        lambda *a, content_dir=None, **kw: synced.append(content_dir) or _Report(),
    )
    topic = SimpleNamespace(confluence_pages=lambda: [])
    monkeypatch.setattr("policyforge.topics.registry.load_topics", lambda _p: [topic])
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    return synced


def test_a_host_alone_does_not_widen_the_corpus_to_docs(tmp_path, monkeypatch):
    """Unrelated markdown in `docs/` would be answered from, and a page nobody
    reviewed would become a source: with a host and no named tree, no tree."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    synced = _fake_sync(monkeypatch, {"zardoz": {"host": "https://wiki.example"}})
    result = CliRunner().invoke(cli_mod.cli, ["zardoz", "sync"])
    assert result.exit_code == 0, result.output
    assert synced == [None]


def test_a_host_with_a_named_tree_reads_that_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("docs", "team_docs", "from_flag"):
        (tmp_path / name).mkdir()
    config = {"zardoz": {"host": "https://wiki.example", "content_dir": "team_docs"}}
    synced = _fake_sync(monkeypatch, config)
    runner = CliRunner()
    assert runner.invoke(cli_mod.cli, ["zardoz", "sync"]).exit_code == 0
    assert (
        runner.invoke(cli_mod.cli, ["zardoz", "sync", "--content-dir", "from_flag"]).exit_code == 0
    )
    assert synced == [Path("team_docs"), Path("from_flag")]


def test_with_no_host_the_default_tree_is_read_only_when_it_is_there(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    synced = _fake_sync(monkeypatch, {})
    runner = CliRunner()
    nothing = runner.invoke(cli_mod.cli, ["zardoz", "sync"])
    assert nothing.exit_code != 0
    assert synced == []
    (tmp_path / "docs").mkdir()
    assert runner.invoke(cli_mod.cli, ["zardoz", "sync"]).exit_code == 0
    assert synced == [Path("docs")]


def test_a_named_tree_that_is_missing_is_an_error_even_with_a_host(tmp_path, monkeypatch):
    """Named and absent is a mistake to report, not a reason to sync the host
    alone and say nothing."""
    monkeypatch.chdir(tmp_path)
    config = {"zardoz": {"host": "https://wiki.example", "content_dir": "nowhere"}}
    synced = _fake_sync(monkeypatch, config)
    result = CliRunner().invoke(cli_mod.cli, ["zardoz", "sync"])
    assert result.exit_code != 0
    assert "No content directory at nowhere" in result.output
    assert synced == []


def test_a_host_with_only_the_flag_reads_the_flags_tree(tmp_path, monkeypatch):
    """The flag names a tree by itself, with no `zardoz.content_dir` beside it:
    a host in the config must not make `--content-dir` a no-op."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "from_flag").mkdir()
    synced = _fake_sync(monkeypatch, {"zardoz": {"host": "https://wiki.example"}})
    result = CliRunner().invoke(cli_mod.cli, ["zardoz", "sync", "--content-dir", "from_flag"])
    assert result.exit_code == 0, result.output
    assert synced == [Path("from_flag")]

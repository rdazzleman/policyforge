"""`generate` checks its own arguments before it builds a provider (#220).

The provider was built first, so `generate --tier procedure` with no
`--standard`, on a machine with no provider configured, failed on the provider
instead of saying `--standard` is required; with one configured, it built a
client it never used. Every check that needs no model now runs first. Derived
from `generate_cmd`, they are the three that read only the arguments:

- `--standard` is required for a Policy or a Procedure;
- the Standard file can be read as text (`click.Path(exists=True)` still
  accepts a directory);
- the Standard has a `# ` title, which the Policy or Procedure cites by name.

Proven with a provider factory that raises: reaching it fails the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

SYNTHESIS = "---\ntopic: Access Control\n---\n\n- Staff must review access. [NIST 800-53 AC-2]\n"
STANDARD = "# Access Control Standard\n\nStaff must review access. [NIST 800-53 AC-2]\n"
DRAFT = "# Access Control Policy\n\nStaff must review access. [NIST 800-53 AC-2]\n"


class _ProviderBuilt(Exception):
    """Raised where the provider would be built."""


def _unbuildable(config):
    raise _ProviderBuilt


class _Draft:
    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.prompts.append(prompt)
        return LLMResponse(text=DRAFT, model="fake")

    def check(self):
        return True


def _run(tmp_path, monkeypatch, *args, factory=_unbuildable):
    import policyforge.cli as cli_mod

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(SYNTHESIS, encoding="utf-8")
    config = {"org": {"name": "Acme", "industry": "Health"}, "llm": {"provider": "local"}}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", factory)
    return CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--synthesis",
            str(synthesis),
            "--out",
            str(tmp_path / "out" / "access-control.md"),
            "--history-dir",
            str(tmp_path / "history"),
            *args,
        ],
    )


def _standard(tmp_path: Path, text: str | bytes) -> Path:
    path = tmp_path / "standard.md"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("tier", ["policy", "procedure"])
def test_a_missing_standard_is_reported_before_any_provider_is_built(tmp_path, monkeypatch, tier):
    result = _run(tmp_path, monkeypatch, "--tier", tier)
    assert not isinstance(result.exception, _ProviderBuilt), result.output
    assert result.exit_code == 2, (result.exit_code, result.output)
    assert f"--standard is required when --tier {tier}" in result.output, result.output


def test_a_standard_that_is_a_directory_is_reported_before_any_provider_is_built(
    tmp_path, monkeypatch
):
    directory = tmp_path / "standards"
    directory.mkdir()
    result = _run(tmp_path, monkeypatch, "--tier", "policy", "--standard", str(directory))
    assert not isinstance(result.exception, _ProviderBuilt), result.output
    assert result.exit_code == 2, (result.exit_code, result.output)
    assert "can't be read" in result.output, result.output


def test_a_standard_that_is_not_text_is_reported_before_any_provider_is_built(
    tmp_path, monkeypatch
):
    standard = _standard(tmp_path, b"\xff\xfe\x00not utf-8 \xff")
    result = _run(tmp_path, monkeypatch, "--tier", "policy", "--standard", str(standard))
    assert not isinstance(result.exception, _ProviderBuilt), result.output
    assert result.exit_code == 2, (result.exit_code, result.output)
    assert "can't be read" in result.output, result.output


def test_a_standard_with_no_title_is_reported_before_any_provider_is_built(tmp_path, monkeypatch):
    standard = _standard(tmp_path, "Staff must review access.\n")
    result = _run(tmp_path, monkeypatch, "--tier", "procedure", "--standard", str(standard))
    assert not isinstance(result.exception, _ProviderBuilt), result.output
    assert result.exit_code == 2, (result.exit_code, result.output)
    assert "has no '# ' title" in result.output, result.output


@pytest.mark.parametrize(
    "args",
    [["--tier", "standard"], ["--tier", "policy", "--standard", "STANDARD"]],
    ids=["standard", "policy"],
)
def test_valid_arguments_still_reach_the_provider(tmp_path, monkeypatch, args):
    """The control case: with nothing wrong, the unbuildable provider IS what
    stops the run, so the tests above prove the checks come first."""
    standard = _standard(tmp_path, STANDARD)
    args = [str(standard) if a == "STANDARD" else a for a in args]
    result = _run(tmp_path, monkeypatch, *args)
    assert isinstance(result.exception, _ProviderBuilt), (result.exception, result.output)


def test_valid_arguments_draft_as_before(tmp_path, monkeypatch):
    """The passing case, end to end with a fake provider: a Policy is drafted,
    and the title read from the Standard before the provider was built is the
    one its prompt names."""
    from policyforge.llm import ledger

    standard = _standard(tmp_path, STANDARD)
    draft = _Draft()
    result = _run(
        tmp_path,
        monkeypatch,
        "--tier",
        "policy",
        "--standard",
        str(standard),
        factory=lambda config: ledger.wrap(draft, config),
    )
    assert result.exit_code == 0, result.output
    assert draft.prompts, "the provider was called"
    assert any("Access Control Standard" in p for p in draft.prompts), draft.prompts
    assert (tmp_path / "out" / "access-control.md").exists()
    assert "Drafted policy" in result.output

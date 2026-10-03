"""S-04 — the content class survives the hand-off from synthesize to generate.

`synthesize` refuses to send a licensed catalog to a hosted model, and
records the class on its own ledger entry. Then it used to write the merged
requirement text to `output/synthesis/` with no class at all, `classify_path`
read that file as the organization's own, and `generate` sent it to whatever
provider was configured. A synthesis drawn from a HITRUST export is a
restatement of HITRUST requirement text; the boundary held for one command
and let go at the next.

These tests follow the text across the hand-off: the class is written into
the synthesis, `generate` reads it and refuses the same pairing `synthesize`
would, and the class reaches the ledger scope the document is drafted in.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from policyforge.llm import ledger

HOSTED = {"provider": "anthropic", "model": "claude-sonnet-5"}
LOCAL_LLM = {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen3"}


class FakeProvider:
    def __init__(
        self, text="# Access Control Standard\n\nStaff must review access. [HITRUST 01.a]\n"
    ):
        self.text = text
        self.calls = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls.append(prompt)
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def _licensed_catalog(tmp_path):
    """A controls.json where this project puts licensed exports.

    Labelled as 800-53 so `--nist-controls IA-5` selects it. The class does
    not come from that label: it comes from where the file lives, which is
    the point — `local_content/` is licensed whatever the file says inside.
    """
    export_dir = tmp_path / "local_content"
    export_dir.mkdir()
    path = export_dir / "hitrust-controls.json"
    path.write_text(
        json.dumps(
            [
                {
                    "control_id": "IA-5",
                    "title": "Authenticator Management",
                    "framework": "NIST 800-53",
                    "framework_version": "Rev 5",
                    "control_statement": "Licensed requirement text.",
                }
            ]
        ),
        encoding="utf-8",
    )
    return export_dir, path


# --------------------------------------------------------------------------
# The file carries the class
# --------------------------------------------------------------------------


def test_the_class_and_its_sources_are_written_into_the_synthesis():
    from policyforge.synthesis.merge import read_synthesis, write_synthesis

    text = write_synthesis(
        "- A requirement.",
        topic="Access Control",
        content_class="licensed",
        derived_from=["hitrust-controls.json"],
    )
    metadata, _ = read_synthesis(text)

    assert metadata["content_class"] == "licensed"
    assert metadata["derived_from"] == ["hitrust-controls.json"]


def test_a_call_with_nothing_to_record_still_writes_no_frontmatter():
    from policyforge.synthesis.merge import write_synthesis

    assert not write_synthesis("- A requirement.", topic="").startswith("---")


def test_synthesize_records_that_a_licensed_catalog_fed_the_topic(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from policyforge.synthesis.merge import read_synthesis

    export_dir, catalog = _licensed_catalog(tmp_path)
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    config = {"llm": LOCAL_LLM, "frameworks": {"search_paths": [str(export_dir)]}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod, "get_provider", lambda c: FakeProvider(text="- merged [HITRUST 01.a]")
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Access Control",
            "--nist-controls",
            "IA-5",
            "--controls",
            str(catalog),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )

    assert result.exit_code == 0, result.output
    metadata, _ = read_synthesis(
        (tmp_path / "synthesis" / "access-control.md").read_text(encoding="utf-8")
    )
    assert metadata["content_class"] == "licensed"
    assert metadata["derived_from"] == ["hitrust-controls.json"]


# --------------------------------------------------------------------------
# generate reads it and holds the same line
# --------------------------------------------------------------------------


def _generate(tmp_path, monkeypatch, synthesis_text, *, llm, provider=None, boundary_cfg=None):
    import policyforge.cli as cli_mod

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(synthesis_text, encoding="utf-8")
    config = {"org": {"name": "Acme", "industry": "Health"}, "llm": dict(llm)}
    if boundary_cfg:
        config["llm"]["boundary"] = boundary_cfg
    fake = provider or FakeProvider()
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    # Wrapped, so what the drafting records can be read back.
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: ledger.wrap(fake, c))
    out = tmp_path / "standards" / "access-control.md"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(synthesis),
            "--out",
            str(out),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    return result, fake, out


LICENSED_SYNTHESIS = (
    "---\ntopic: Access Control\ncontent_class: licensed\n"
    "derived_from:\n- hitrust-controls.json\n---\n\n"
    "- Licensed-derived requirement. [HITRUST 01.a]\n"
)


def test_a_licensed_synthesis_is_refused_to_a_hosted_model(tmp_path, monkeypatch):
    result, fake, out = _generate(tmp_path, monkeypatch, LICENSED_SYNTHESIS, llm=HOSTED)

    assert result.exit_code != 0
    assert "REFUSED" in result.output
    assert "hitrust-controls.json" in result.output, "the refusal names where it came from"
    assert fake.calls == [], "nothing was sent"
    assert not out.exists()


def test_a_licensed_synthesis_may_be_drafted_by_a_local_model(tmp_path, monkeypatch):
    result, fake, out = _generate(tmp_path, monkeypatch, LICENSED_SYNTHESIS, llm=LOCAL_LLM)

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert len(fake.calls) == 1


def test_the_class_reaches_the_ledger_scope_the_document_is_drafted_in(tmp_path, monkeypatch):
    """So the record of the call says what it carried, and so does the
    provenance stamped into the document's version history."""
    _generate(tmp_path, monkeypatch, LICENSED_SYNTHESIS, llm=LOCAL_LLM)

    records = ledger.load(tmp_path / "calls.jsonl")
    assert records and all(r.content_class == "licensed" for r in records)
    assert all(r.site == "generate" for r in records)


def test_a_synthesis_written_before_the_class_travelled_is_classified_as_before(
    tmp_path, monkeypatch
):
    result, fake, _ = _generate(tmp_path, monkeypatch, "- Requirement. [NIST IA-5]\n", llm=HOSTED)

    assert result.exit_code == 0, result.output
    assert len(fake.calls) == 1


def test_a_tightened_ceiling_applies_to_generate_too(tmp_path, monkeypatch):
    """The same check `synthesize` runs, not a second weaker one."""
    synthesis = "---\ncontent_class: organization-internal\n---\n\n- Requirement. [NIST IA-5]\n"
    result, fake, _ = _generate(
        tmp_path,
        monkeypatch,
        synthesis,
        llm=HOSTED,
        boundary_cfg={"organization-internal": "self-hosted"},
    )

    assert result.exit_code != 0
    assert fake.calls == []


@pytest.mark.parametrize("value", ["secret", "LICENSED-ISH", ""])
def test_a_class_that_is_not_a_class_is_refused_rather_than_guessed(tmp_path, monkeypatch, value):
    synthesis = f"---\ncontent_class: '{value}'\n---\n\n- Requirement.\n"
    result, fake, _ = _generate(tmp_path, monkeypatch, synthesis, llm=LOCAL_LLM)

    assert result.exit_code != 0
    assert "not a content class" in result.output
    assert fake.calls == []


# --------------------------------------------------------------------------
# Found on the way: the class of a multi-catalog run
# --------------------------------------------------------------------------


def test_the_most_restrictive_input_decides_the_class(tmp_path, monkeypatch):
    """It read "licensed if any input is, otherwise the first input's class",
    so 800-53 named first labelled a run public domain even when a later
    catalog was the organization's own."""
    from pathlib import Path

    import policyforge.cli as cli_mod
    from policyforge.llm import boundary
    from policyforge.synthesis.merge import read_synthesis

    public, internal = tmp_path / "nist.json", tmp_path / "internal.json"
    for path in (public, internal):
        path.write_text(
            json.dumps(
                [
                    {
                        "control_id": "IA-5",
                        "title": "Authenticator Management",
                        "framework": "NIST 800-53",
                        "framework_version": "Rev 5",
                        "control_statement": "Manage authenticators.",
                    }
                ]
            ),
            encoding="utf-8",
        )
    classes = {public.name: boundary.PUBLIC_DOMAIN, internal.name: boundary.ORG_INTERNAL}
    monkeypatch.setattr(
        boundary,
        "classify_path",
        lambda path, config=None: boundary.ContentClassification(classes[Path(path).name], "t"),
    )
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: FakeProvider(text="- merged"))

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Access Control",
            "--nist-controls",
            "IA-5",
            "--controls",
            str(public),
            "--controls",
            str(internal),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )

    assert result.exit_code == 0, result.output
    metadata, _ = read_synthesis(
        (tmp_path / "synthesis" / "access-control.md").read_text(encoding="utf-8")
    )
    assert metadata["content_class"] == boundary.ORG_INTERNAL
    assert metadata["derived_from"] == ["nist.json", "internal.json"]

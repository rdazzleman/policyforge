"""The registry -> synthesize -> generate hand-off.

The topic registry knows who owns a process; the generated document has to
say so. That information crosses a file boundary between two separate
commands, so these tests cover the whole path: `synthesize --topic-name`
resolving a registry topic and recording its owner in the synthesis file's
frontmatter, and `generate` reading it back into the drafting prompt.

Also covers `map --controls` accepting several files, since a
*cross*-framework crosswalk built from one framework's file has nothing to
cross.

Like tests/test_cli.py, everything here fakes the LLM provider and config —
no network calls, no API keys.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from policyforge.mapping.crosswalk import NIST_ANCHOR


class FakeProvider:
    def __init__(self, text="- merged requirement [NIST IA-5]"):
        self.text = text
        self.calls = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def _control(**overrides):
    base = {
        "control_id": "IA-5",
        "title": "Authenticator Management",
        "framework": "NIST 800-53",
        "framework_version": "Rev 5",
        "family": None,
        "family_abbr": None,
        "baseline": None,
        "control_statement": "Manage system authenticators.",
        "discussion": "",
        "enhancements": [],
        "related_controls": [],
        "source_crosswalk": {},
        "source_path": None,
    }
    base.update(overrides)
    return base


def _fixtures(tmp_path, monkeypatch, provider=None):
    """Controls file, empty crosswalk, registry, and a patched CLI module."""
    import policyforge.cli as cli_mod

    controls_path = tmp_path / "controls.json"
    controls_path.write_text(json.dumps([_control()]), encoding="utf-8")
    crosswalk_path = tmp_path / "crosswalk.json"
    crosswalk_path.write_text(json.dumps({}), encoding="utf-8")
    registry_path = tmp_path / "topics.yaml"
    registry_path.write_text(
        "topics:\n"
        "  - name: Media Handling\n"
        "    owner: IT Asset Management\n"
        "    cadence: quarterly\n"
        "    nist_controls: [IA-5]\n"
        "    evidence:\n"
        "      - Certificates of destruction\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_mod, "load_config", lambda: {"org": {"name": "Acme", "industry": "H"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider or FakeProvider())

    common = [
        "--controls",
        str(controls_path),
        "--crosswalk",
        str(crosswalk_path),
        "--out-dir",
        str(tmp_path / "synthesis"),
    ]
    return cli_mod, registry_path, common


# --------------------------------------------------------------------------
# The frontmatter contract
# --------------------------------------------------------------------------


def test_synthesis_frontmatter_round_trips():
    from policyforge.synthesis.merge import read_synthesis, write_synthesis

    text = write_synthesis(
        "- A requirement. [NIST IA-5]",
        topic="Media Handling",
        owner="IT Asset Management",
        cadence="quarterly",
        evidence=["Certificates of destruction"],
        nist_controls=["MP-6"],
    )
    metadata, body = read_synthesis(text)

    assert metadata == {
        "topic": "Media Handling",
        "owner": "IT Asset Management",
        "cadence": "quarterly",
        "evidence": ["Certificates of destruction"],
        "nist_controls": ["MP-6"],
    }
    assert body.strip() == "- A requirement. [NIST IA-5]"


def test_ad_hoc_synthesis_writes_no_frontmatter_block():
    """A topic that isn't in the registry has no owner to record, so the file
    stays exactly as it was before frontmatter existed."""
    from policyforge.synthesis.merge import write_synthesis

    text = write_synthesis("- A requirement.", topic="")
    assert not text.startswith("---")


def test_reading_a_file_without_frontmatter_yields_empty_metadata():
    from policyforge.synthesis.merge import read_synthesis

    metadata, body = read_synthesis("- A requirement. [NIST IA-5]\n")
    assert metadata == {}
    assert body.strip() == "- A requirement. [NIST IA-5]"


# --------------------------------------------------------------------------
# Prompt context
# --------------------------------------------------------------------------


def test_prompt_context_is_unchanged_when_no_owner_is_known():
    from policyforge.generate.policy_writer import (
        OrgContext,
        TopicContext,
        _render_context,
        _render_org,
    )

    org = OrgContext(name="Acme", industry="Healthcare", vendors=["Okta"])
    assert _render_context(org, None) == _render_org(org)
    assert _render_context(org, TopicContext(name="T")) == _render_org(org)


def test_prompt_context_names_the_owner_and_forbids_the_placeholder():
    from policyforge.generate.policy_writer import OrgContext, TopicContext, _render_context

    rendered = _render_context(
        OrgContext(name="Acme", industry="Healthcare"),
        TopicContext(
            name="Media Handling",
            owner="IT Asset Management",
            cadence="quarterly",
            evidence=["Certificates of destruction"],
        ),
    )

    assert "IT Asset Management" in rendered
    assert "Do not write [Responsible Team]" in rendered
    assert "quarterly" in rendered
    assert "Certificates of destruction" in rendered


def test_each_generator_accepts_topic_context():
    from policyforge.generate.policy_writer import (
        OrgContext,
        TopicContext,
        generate_policy,
        generate_procedure,
        generate_standard,
    )

    org = OrgContext(name="Acme", industry="Healthcare")
    topic = TopicContext(name="Media Handling", owner="IT Asset Management")

    for call in (
        lambda p: generate_standard("- req [NIST MP-6]", org, p, topic=topic),
        lambda p: generate_policy("- req [NIST MP-6]", org, p, standard_title="S", topic=topic),
        lambda p: generate_procedure("- req [NIST MP-6]", org, p, standard_title="S", topic=topic),
    ):
        provider = FakeProvider(text="# T\n\nBody.")
        call(provider)
        assert "IT Asset Management" in provider.calls[0]["prompt"]


# --------------------------------------------------------------------------
# synthesize --topic-name
# --------------------------------------------------------------------------


def test_synthesize_from_registry_records_the_owner(tmp_path, monkeypatch):
    from policyforge.synthesis.merge import read_synthesis

    cli_mod, registry, common = _fixtures(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["synthesize", "--topic-name", "Media Handling", "--topics", str(registry), *common],
    )

    assert result.exit_code == 0, result.output
    assert "IT Asset Management" in result.output
    metadata, body = read_synthesis(
        (tmp_path / "synthesis" / "media-handling.md").read_text(encoding="utf-8")
    )
    assert metadata["owner"] == "IT Asset Management"
    assert metadata["cadence"] == "quarterly"
    assert metadata["nist_controls"] == ["IA-5"]
    assert "merged requirement" in body


def test_topic_name_matches_case_insensitively(tmp_path, monkeypatch):
    cli_mod, registry, common = _fixtures(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["synthesize", "--topic-name", "media handling", "--topics", str(registry), *common],
    )
    assert result.exit_code == 0, result.output


def test_unknown_topic_name_lists_what_is_available(tmp_path, monkeypatch):
    cli_mod, registry, common = _fixtures(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["synthesize", "--topic-name", "Nope", "--topics", str(registry), *common],
    )

    assert result.exit_code != 0
    assert "Media Handling" in result.output


def test_registry_and_ad_hoc_options_are_mutually_exclusive(tmp_path, monkeypatch):
    cli_mod, registry, common = _fixtures(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic-name",
            "Media Handling",
            "--topics",
            str(registry),
            "--topic",
            "Something else",
            *common,
        ],
    )

    assert result.exit_code != 0
    assert "don" in result.output and "also pass" in result.output


def test_synthesize_requires_one_of_the_two_modes(tmp_path, monkeypatch):
    cli_mod, _, common = _fixtures(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli_mod.cli, ["synthesize", *common])

    assert result.exit_code != 0
    assert "--topic-name" in result.output


def test_ad_hoc_synthesis_still_works_and_warns_about_the_missing_owner(tmp_path, monkeypatch):
    cli_mod, _, common = _fixtures(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["synthesize", "--topic", "One Off", "--nist-controls", "IA-5", *common],
    )

    assert result.exit_code == 0, result.output
    assert "No owning team recorded" in result.output


# --------------------------------------------------------------------------
# generate reads the frontmatter back
# --------------------------------------------------------------------------


def _generate(tmp_path, monkeypatch, synthesis_text, provider):
    import policyforge.cli as cli_mod

    synthesis_path = tmp_path / "media-handling.md"
    synthesis_path.write_text(synthesis_text, encoding="utf-8")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"org": {"name": "Acme", "industry": "H"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)

    return CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(synthesis_path),
            "--out",
            str(tmp_path / "out" / "media-handling.md"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )


def test_generate_puts_the_frontmatter_owner_into_the_prompt(tmp_path, monkeypatch):
    """The payoff: the drafting prompt names the real team, so the document
    doesn't fall back to a [Responsible Team] placeholder."""
    provider = FakeProvider(text="# Media Handling Standard\n\nBody.\n")
    result = _generate(
        tmp_path,
        monkeypatch,
        "---\ntopic: Media Handling\nowner: IT Asset Management\ncadence: quarterly\n"
        "evidence:\n- Certificates of destruction\n---\n\n"
        "- Media must be sanitized before disposal. [NIST MP-6]\n",
        provider,
    )

    assert result.exit_code == 0, result.output
    assert "Topic owner from synthesis frontmatter: IT Asset Management" in result.output

    prompt = provider.calls[0]["prompt"]
    assert "IT Asset Management" in prompt
    assert "Do not write [Responsible Team]" in prompt
    assert "quarterly" in prompt
    assert "Certificates of destruction" in prompt


def test_generate_does_not_leak_raw_frontmatter_into_the_prompt(tmp_path, monkeypatch):
    """The YAML block is metadata for the pipeline, not requirement text for
    the model to draft from."""
    provider = FakeProvider(text="# T\n\nBody.\n")
    _generate(
        tmp_path,
        monkeypatch,
        "---\ntopic: Media Handling\nowner: IT Asset Management\n---\n\n"
        "- Media must be sanitized. [NIST MP-6]\n",
        provider,
    )

    requirements = provider.calls[0]["prompt"].split("Synthesized requirements")[-1]
    assert "---" not in requirements
    assert "topic: Media Handling" not in requirements
    assert "Media must be sanitized. [NIST MP-6]" in requirements


def test_generate_still_works_without_frontmatter(tmp_path, monkeypatch):
    """Synthesis files written before the registry existed must still draft —
    with the placeholder behaviour they had before."""
    provider = FakeProvider(text="# Legacy Standard\n\nBody.\n")
    result = _generate(tmp_path, monkeypatch, "- A requirement. [NIST IA-5]\n", provider)

    assert result.exit_code == 0, result.output
    assert "No owner in the synthesis frontmatter" in result.output
    assert "A requirement. [NIST IA-5]" in provider.calls[0]["prompt"]


# --------------------------------------------------------------------------
# map over several frameworks
# --------------------------------------------------------------------------


def test_map_accepts_multiple_controls_files(tmp_path):
    from policyforge.cli import cli

    nist_path = tmp_path / "nist.json"
    nist_path.write_text(json.dumps([_control()]), encoding="utf-8")
    hipaa_path = tmp_path / "hipaa.json"
    hipaa_path.write_text(
        json.dumps(
            [
                _control(
                    control_id="164.312(a)(1)",
                    framework="HIPAA Security Rule",
                    source_crosswalk={NIST_ANCHOR: "IA-5"},
                )
            ]
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "crosswalk.json"

    result = CliRunner().invoke(
        cli,
        [
            "map",
            "--controls",
            str(nist_path),
            "--controls",
            str(hipaa_path),
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(out_path.read_text(encoding="utf-8")) == {
        "IA-5": {"hipaa": ["164.312(a)(1)"]}
    }
    assert "Frameworks mapped: hipaa" in result.output


def test_map_says_so_when_nothing_crosses(tmp_path):
    """Passing one framework's file used to produce an empty crosswalk in
    silence."""
    from policyforge.cli import cli

    controls_path = tmp_path / "controls.json"
    controls_path.write_text(json.dumps([_control()]), encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["map", "--controls", str(controls_path), "--out", str(tmp_path / "cw.json")]
    )

    assert result.exit_code == 0
    assert "No cross-framework mappings found" in result.output

"""Smoke test so CI has something to run. Replace/expand as real modules
land (mapping, synthesis, generate)."""

from policyforge.mapping.crosswalk import NIST_ANCHOR


def test_package_imports():
    import policyforge  # noqa: F401


def test_markdown_quality_check(tmp_path):
    from policyforge.export.markdown_exporter import check_markdown_quality, write_markdown

    good = write_markdown(
        "# Title\n\nSome well-formed text.\n", output_dir=tmp_path, filename="good.md"
    )
    assert check_markdown_quality(good) is True

    # Sloppy formatting (extra blank lines, inconsistent bullet markers)
    # should fail the check rather than silently pass as "primary
    # deliverable quality" output.
    bad = write_markdown("# Title\n\n\n\n* one\n- two\n", output_dir=tmp_path, filename="bad.md")
    assert check_markdown_quality(bad) is False


def test_nist_vault_loader_parses_fixture(tmp_path):
    from policyforge.ingest.nist_vault_loader import parse_control_file

    fixture = tmp_path / "AC-2.md"
    fixture.write_text(
        """---
type: control
control_id: AC-2
family: Access Control
family_abbr: AC
title: Account Management
framework: NIST 800-53
version: Rev 5
baseline: Low, Moderate, High
status: not-implemented
priority: ""
tags: [nist, 800-53, ac, control]
---

# AC-2: Account Management

## Control Statement

> Manage information system accounts.

## Discussion

Account management covers the full lifecycle.

## Control Enhancements

| Enhancement | Title | Baseline | Description |
|-------------|-------|----------|-------------|
| **AC-2(1)** | Automated Account Management | Moderate, High | Support automated mechanisms. |

## Related Controls

[[AC-3]], [[AC-5]]

## Cross-Framework Mappings

| Framework | Equivalent |
|-----------|----------|
| FedRAMP | AC-2 (same ID) |
| HITRUST CSF | placeholder-ref-not-real-hitrust-data |
""",
        encoding="utf-8",
    )
    # Note: the HITRUST row value above is intentionally a nonsense placeholder,
    # not a real HITRUST control reference. This test only needs to prove the
    # column gets stripped by default — it should never contain anything that
    # looks like it was pulled from an actual HITRUST/MyCSF export.

    control = parse_control_file(fixture)

    assert control.control_id == "AC-2"
    assert control.title == "Account Management"
    assert "Manage information system" in control.control_statement
    assert len(control.enhancements) == 1
    assert control.enhancements[0].enhancement_id == "AC-2(1)"
    assert control.related_controls == ["AC-3", "AC-5"]
    # FedRAMP is in the default-safe crosswalk column set...
    assert control.source_crosswalk.get("fedramp") == "AC-2 (same ID)"
    # ...HITRUST is not, by default, even though it was present in the source table.
    assert "hitrust" not in control.source_crosswalk


def test_build_crosswalk_from_nist_source_crosswalk():
    from policyforge.ingest.schema import Control
    from policyforge.mapping.crosswalk import build_crosswalk

    controls = [
        Control(
            control_id="AC-2",
            title="Account Management",
            framework="NIST 800-53",
            framework_version="Rev 5",
            source_crosswalk={"fedramp": "AC-2 (same ID)"},
        ),
        Control(
            control_id="IA-5",
            title="Authenticator Management",
            framework="NIST 800-53",
            framework_version="Rev 5",
            source_crosswalk={"fedramp": "IA-5, IA-5(1)"},
        ),
    ]

    crosswalk = build_crosswalk(controls)

    assert crosswalk == {
        "AC-2": {"fedramp": ["AC-2"]},
        "IA-5": {"fedramp": ["IA-5", "IA-5(1)"]},
    }


def test_build_crosswalk_folds_in_non_nist_controls_pointing_back():
    from policyforge.ingest.schema import Control
    from policyforge.mapping.crosswalk import build_crosswalk

    controls = [
        Control(
            control_id="AC-2",
            title="Account Management",
            framework="NIST 800-53",
            framework_version="Rev 5",
        ),
        Control(
            control_id="ARC-AC-2",
            title="Account Management",
            framework="ARC-AMPE",
            framework_version="v1",
            source_crosswalk={NIST_ANCHOR: "AC-2 (same ID)"},
        ),
    ]

    crosswalk = build_crosswalk(controls)

    assert crosswalk == {"AC-2": {"arc-ampe": ["ARC-AC-2"]}}


def test_build_crosswalk_reads_enhancement_level_source_crosswalk():
    """A framework may be mapped at the sub-requirement level rather than the
    control level (NIST's HIPAA-to-800-53 crosswalk maps most rows to
    individual implementation specifications). Those must reach the crosswalk
    under the enhancement's own ID, not be folded into the parent's."""
    from policyforge.ingest.schema import Control, ControlEnhancement
    from policyforge.mapping.crosswalk import build_crosswalk

    controls = [
        Control(
            control_id="STD-1",
            title="A standard",
            framework="ExampleFramework",
            framework_version="v1",
            source_crosswalk={NIST_ANCHOR: "AC-1"},
            enhancements=[
                ControlEnhancement(
                    enhancement_id="STD-1(a)",
                    title="A specification",
                    baseline="Required",
                    description="",
                    source_crosswalk={NIST_ANCHOR: "AC-2, AC-3"},
                ),
                ControlEnhancement(
                    enhancement_id="STD-1(b)",
                    title="An unmapped specification",
                    baseline="Addressable",
                    description="",
                ),
            ],
        )
    ]

    crosswalk = build_crosswalk(controls)

    assert crosswalk == {
        "AC-1": {"exampleframework": ["STD-1"]},
        "AC-2": {"exampleframework": ["STD-1(a)"]},
        "AC-3": {"exampleframework": ["STD-1(a)"]},
    }


def test_load_controls_accepts_json_written_before_enhancement_crosswalks(tmp_path):
    """`ControlEnhancement.source_crosswalk` was added after the first
    controls.json files were generated — older data must still load."""
    import json

    from policyforge.ingest.schema import load_controls

    path = tmp_path / "controls.json"
    path.write_text(
        json.dumps(
            [
                {
                    "control_id": "AC-2",
                    "title": "Account Management",
                    "framework": "NIST 800-53",
                    "framework_version": "Rev 5",
                    "enhancements": [
                        {
                            "enhancement_id": "AC-2(1)",
                            "title": "Automated System Account Management",
                            "baseline": "Moderate, High",
                            "description": "Support automated mechanisms.",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_controls(path)

    assert loaded[0].enhancements[0].source_crosswalk == {}


def test_load_controls_round_trips_through_json(tmp_path):
    import dataclasses
    import json

    from policyforge.ingest.schema import Control, ControlEnhancement, load_controls

    original = Control(
        control_id="AC-2",
        title="Account Management",
        framework="NIST 800-53",
        framework_version="Rev 5",
        enhancements=[
            ControlEnhancement(
                enhancement_id="AC-2(1)",
                title="Automated System Account Management",
                baseline="Moderate, High",
                description="Support automated mechanisms.",
            )
        ],
    )
    path = tmp_path / "controls.json"
    path.write_text(json.dumps([dataclasses.asdict(original)]), encoding="utf-8")

    loaded = load_controls(path)

    assert loaded == [original]


def test_build_synthesis_topic_pulls_crosswalk_linked_controls():
    from policyforge.ingest.schema import Control
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.synthesis.merge import build_synthesis_topic

    nist = Control(
        control_id="IA-5",
        title="Authenticator Management",
        framework="NIST 800-53",
        framework_version="Rev 5",
        source_crosswalk={"fedramp": "IA-5 (same ID)"},
    )
    fedramp = Control(
        control_id="IA-5",
        title="Authenticator Management",
        framework="FedRAMP",
        framework_version="Rev 5",
    )
    unrelated = Control(
        control_id="AC-2",
        title="Account Management",
        framework="NIST 800-53",
        framework_version="Rev 5",
    )
    controls = [nist, fedramp, unrelated]

    topic = build_synthesis_topic("Auth", ["IA-5"], controls, build_crosswalk(controls))

    assert topic.controls == [nist, fedramp]


def test_synthesize_topic_grounds_prompt_in_source_controls():
    from policyforge.ingest.schema import Control
    from policyforge.llm.base import LLMResponse
    from policyforge.synthesis.merge import SynthesisTopic, synthesize_topic

    class FakeProvider:
        def __init__(self):
            self.calls = []

        def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
            self.calls.append({"system": system, "prompt": prompt})
            return LLMResponse(text="- merged requirement [NIST IA-5]\n", model="fake")

        def check(self):
            return True

    control = Control(
        control_id="IA-5",
        title="Authenticator Management",
        framework="NIST 800-53",
        framework_version="Rev 5",
        control_statement="Manage authenticators.",
    )
    provider = FakeProvider()

    result = synthesize_topic(SynthesisTopic(name="Auth", controls=[control]), provider)

    assert result == "- merged requirement [NIST IA-5]"
    assert len(provider.calls) == 1
    assert "Manage authenticators." in provider.calls[0]["prompt"]


def test_synthesize_topic_rejects_empty_topic():
    from policyforge.synthesis.merge import SynthesisTopic, synthesize_topic

    try:
        synthesize_topic(SynthesisTopic(name="Empty", controls=[]), provider=None)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an empty topic")


def test_markdown_to_confluence_converts_without_network_or_llm():
    from policyforge.export.confluence_exporter import markdown_to_confluence

    storage_format = markdown_to_confluence(
        "# Authenticator Management Policy\n\n"
        "Acme Corp staff must manage authenticators. [NIST IA-5]\n"
    )

    assert "<h1>Authenticator Management Policy</h1>" in storage_format
    assert "Acme Corp staff must manage authenticators." in storage_format


def test_export_to_confluence_requires_api_token(monkeypatch):
    from policyforge.export.confluence_exporter import export_to_confluence

    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    try:
        export_to_confluence(
            "# Title\n", space="ENG", title="Test Page", host="https://example.atlassian.net/wiki"
        )
    except RuntimeError as exc:
        assert "CONFLUENCE_API_TOKEN" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when no API token is configured")


def test_no_source_file_carries_a_stray_control_character():
    """Twice now a shell heredoc has turned an escape into the character it
    denotes — `\b` became a backspace inside a regex, which then matched
    nothing and silently disabled the check it belonged to. Ruff does not
    flag it and it is invisible in a diff, so it is worth one cheap sweep.
    """
    from pathlib import Path

    offenders = []
    for path in list(Path("src").rglob("*.py")) + list(Path("evals").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        bad = {ord(c) for c in text if ord(c) < 32 and c not in "\n\t"}
        if bad:
            offenders.append(f"{path}: {sorted(hex(b) for b in bad)}")

    assert not offenders, "control characters in source: " + "; ".join(offenders)

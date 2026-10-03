"""CLI-level tests: exercise each `policyforge` command end-to-end through
Click's test runner, faking the LLM provider and config so nothing here
makes a network call."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tests import licensed_fakes


@pytest.fixture(autouse=True)
def _fake_licensed_plugin(monkeypatch):
    """The licensed parsers are a separately distributed plugin (#32); these
    tests pin the public command around them, so a fake answers the parse."""
    licensed_fakes.install(monkeypatch)


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


def _write_controls_json(path, entries):
    path.write_text(json.dumps(entries), encoding="utf-8")


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


def test_llm_check_reports_ok(monkeypatch):
    import policyforge.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"llm": {"provider": "anthropic", "model": "m"}}
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: FakeProvider())

    result = CliRunner().invoke(cli_mod.cli, ["llm-check"])

    assert result.exit_code == 0
    assert "OK" in result.output


def test_etl_vault_parses_fixture_directory(tmp_path):
    from policyforge.cli import cli

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "AC-2.md").write_text(
        """---
control_id: AC-2
title: Account Management
framework: NIST 800-53
version: Rev 5
---

# AC-2: Account Management

## Control Statement

> Manage information system accounts.
""",
        encoding="utf-8",
    )
    out_path = tmp_path / "controls.json"

    result = CliRunner().invoke(
        cli, ["etl-vault", "--controls-dir", str(vault_dir), "--out", str(out_path)]
    )

    assert result.exit_code == 0
    controls = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(controls) == 1
    assert controls[0]["control_id"] == "AC-2"


def test_etl_hipaa_fetches_and_parses(tmp_path, monkeypatch):
    from pathlib import Path

    from policyforge.cli import cli

    fixture = (Path(__file__).parent / "fixtures" / "ecfr_45cfr164_subpart_c.xml").read_text(
        encoding="utf-8"
    )
    monkeypatch.setattr(
        "policyforge.ingest.hipaa_loader.fetch_ecfr_subpart_c_xml", lambda **kwargs: fixture
    )
    # The date lookup runs before the fetch when no --date is given, and was
    # unstubbed: this test called eCFR's versioner live and failed on its 403
    # (#432).
    monkeypatch.setattr("policyforge.ingest.hipaa_loader.current_ecfr_date", lambda: "2026-01-01")
    out_path = tmp_path / "hipaa-controls.json"

    result = CliRunner().invoke(cli, ["etl-hipaa", "--out", str(out_path)])

    assert result.exit_code == 0
    controls = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(controls) == 34
    assert any(c["control_id"] == "164.308(a)(1)(i)" for c in controls)


def test_etl_govramp_refuses_to_write_into_the_bundled_directory(tmp_path):
    """data/frameworks/ is this project's public, redistributable half. A
    licensed catalog written there gets committed and pushed."""
    from policyforge.cli import cli
    from tests.licensed_fakes import build_workbook

    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")

    result = CliRunner().invoke(
        cli,
        [
            "etl-govramp",
            "--export",
            str(matrix),
            "--out",
            "data/frameworks/govramp/controls.json",
        ],
    )

    assert result.exit_code != 0
    assert "never be written there" in result.output


class _NoReport:
    # The real Summary's interface: etl-hitrust appends the loader's losses
    # to `warnings` before printing (#266).
    def __init__(self):
        self.warnings: list[str] = []

    def format_report(self):
        return ""


def _hitrust(monkeypatch, tmp_path, *args):
    """Run etl-hitrust past parsing, to exercise only its write guards.

    The parser is the separately distributed plugin (#32); a fake answers
    the parse, because the guards are what these pin.
    """
    import policyforge.cli as cli_mod

    export = tmp_path / "hitrust-export.csv"
    export.write_text("placeholder", encoding="utf-8")
    licensed_fakes.install(monkeypatch)
    return CliRunner().invoke(cli_mod.cli, ["etl-hitrust", "--export", str(export), *args])


def test_etl_hitrust_refuses_to_write_into_the_bundled_directory(tmp_path, monkeypatch):
    """The HITRUST twin of the GovRAMP refusal, which was the only one tested.

    Pinned before the two copies of this guard were merged, with the
    framework's own wording, so a merge cannot swap "HITRUST export" for
    "GovRAMP matrix" unnoticed.
    """
    result = _hitrust(
        monkeypatch, tmp_path, "--out", "data/frameworks/hitrust/controls.json", "--force"
    )

    assert result.exit_code != 0
    assert "A HITRUST export must never be written there" in result.output


def test_etl_hitrust_refuses_a_tracked_destination_without_permission(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr("policyforge.frameworks.registry.is_ignored", lambda path: False)
    # Licensed to the boundary (#459), so what refuses is the tracked-file rule.
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "local_content" / "export" / "controls.json"

    result = _hitrust(monkeypatch, tmp_path, "--out", str(out))

    assert result.exit_code == 1
    assert "is not gitignored, and this repository has not" in result.output
    assert "if your MyCSF licence permits your repository to carry the export" in result.output
    assert not out.exists()


def test_etl_govramp_refuses_a_tracked_destination_without_permission(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from tests.licensed_fakes import build_workbook

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr("policyforge.frameworks.registry.is_ignored", lambda path: False)
    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")
    # Licensed to the boundary (#459), so what refuses is the tracked-file rule.
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "local_content" / "govramp" / "controls.json"

    result = CliRunner().invoke(
        cli_mod.cli, ["etl-govramp", "--export", str(matrix), "--out", str(out)]
    )

    assert result.exit_code == 1
    assert "is not gitignored, and this repository has not" in result.output
    assert "if your GovRAMP licence permits your repository to carry the matrix" in result.output
    assert not out.exists()


def test_etl_govramp_bundled_directory_refusal_names_the_matrix(tmp_path):
    """The existing test checks the shared phrase; this pins the noun."""
    from policyforge.cli import cli
    from tests.licensed_fakes import build_workbook

    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")
    result = CliRunner().invoke(
        cli,
        ["etl-govramp", "--export", str(matrix), "--out", "data/frameworks/govramp/controls.json"],
    )

    assert result.exit_code != 0
    assert "A GovRAMP matrix must never be written there" in result.output


@pytest.mark.parametrize(
    ("cwd_part", "out_spelling"),
    [
        ("", "data/frameworks/govramp/controls.json"),
        # Case-insensitive filesystems (Windows, macOS by default) resolve
        # these to the same directory. Measured: before this test, both wrote
        # a licensed GovRAMP catalog into the bundled directory with --force.
        ("", "Data/Frameworks/govramp/controls.json"),
        ("", "DATA/frameworks/govramp/controls.json"),
        # Run from inside data/, and the path never mentions data/frameworks.
        ("data", "frameworks/govramp/controls.json"),
        # A detour that comes back.
        ("", "local_content/../data/frameworks/govramp/controls.json"),
    ],
)
def test_a_licensed_catalog_never_lands_in_the_bundled_directory(
    tmp_path, monkeypatch, cwd_part, out_spelling
):
    """The invariant, stated as what must not happen on disk.

    Stated on disk rather than as "the command exits non-zero", because the
    disk is what matters: what must never happen, on any filesystem and with
    --force, is a licensed catalog appearing inside the directory this
    project bundles and pushes.
    The rule used to be a substring test on the path's spelling, which the
    filesystem does not share.
    """
    import policyforge.cli as cli_mod
    from tests.licensed_fakes import build_workbook

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    bundled = tmp_path / "data" / "frameworks"
    (bundled / "govramp").mkdir(parents=True)
    (tmp_path / "local_content").mkdir()
    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")

    monkeypatch.chdir(tmp_path / cwd_part if cwd_part else tmp_path)
    result = CliRunner().invoke(
        cli_mod.cli,
        ["etl-govramp", "--export", str(matrix), "--out", out_spelling, "--force"],
    )

    landed = list(bundled.rglob("*.json"))
    assert landed == [], (
        f"--out {out_spelling!r} from {cwd_part or '.'!r} wrote a licensed catalog into "
        f"the bundled directory: {landed}\n{result.output}"
    )


@pytest.mark.parametrize(
    "spelling",
    [
        ("data", "frameworks"),
        # Found by policyforge-ba: from outside the other checkout, the
        # upward search never finds its data/frameworks, and the spelling
        # test was a case-sensitive substring. Wrote the catalog on Windows.
        ("Data", "Frameworks"),
        ("DATA", "FRAMEWORKS"),
        # A detour through a sibling, spelled in absolute form.
        ("data", "govramp", "..", "frameworks"),
    ],
)
def test_another_checkouts_bundled_directory_is_refused_from_outside_it(
    tmp_path, monkeypatch, spelling
):
    import policyforge.cli as cli_mod
    from tests.licensed_fakes import build_workbook

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    other = tmp_path / "other-checkout"
    bundled = other / "data" / "frameworks"
    (bundled / "govramp").mkdir(parents=True)
    (other / "data" / "govramp").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")
    out = other.joinpath(*spelling, "govramp", "controls.json")

    monkeypatch.chdir(elsewhere)
    result = CliRunner().invoke(
        cli_mod.cli, ["etl-govramp", "--export", str(matrix), "--out", str(out), "--force"]
    )

    assert result.exit_code != 0, result.output
    assert "must never be written there" in result.output
    assert list(other.rglob("*.json")) == []


@pytest.mark.parametrize("out_parts", [("metadata", "frameworks"), ("data", "frameworks-old")])
def test_a_directory_that_only_resembles_the_bundled_one_is_not_refused(
    tmp_path, monkeypatch, out_parts
):
    """Components, not substrings: `metadata/frameworks` is not `data/frameworks`."""
    import policyforge.cli as cli_mod
    from tests.licensed_fakes import build_workbook

    # The lookalike is declared a search path, so the model boundary reads a
    # catalog there as licensed (#459) and only the bundled-name rule is on
    # trial: it must not mistake `metadata/frameworks` for `data/frameworks`.
    root = "/".join(out_parts)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"frameworks": {"search_paths": [root]}})
    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")
    destination = tmp_path.joinpath(*out_parts, "govramp", "controls.json")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli_mod.cli,
        ["etl-govramp", "--export", str(matrix), "--out", str(destination), "--force"],
    )

    assert result.exit_code == 0, result.output
    assert destination.exists()


@pytest.mark.parametrize("cwd_part", ["", "data", "local_content"])
def test_the_bundled_directory_rule_does_not_refuse_a_write_beside_it(
    tmp_path, monkeypatch, cwd_part
):
    """The identity check searches upward for data/frameworks. It must refuse
    only a destination *inside* that directory — a licensed catalog written to
    local_content/, right next to it, is exactly where this project says to
    put one."""
    import policyforge.cli as cli_mod
    from tests.licensed_fakes import build_workbook

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    (tmp_path / "data" / "frameworks" / "govramp").mkdir(parents=True)
    (tmp_path / "local_content").mkdir()
    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")
    destination = tmp_path / "local_content" / "govramp" / "controls.json"

    monkeypatch.chdir(tmp_path / cwd_part if cwd_part else tmp_path)
    result = CliRunner().invoke(
        cli_mod.cli,
        ["etl-govramp", "--export", str(matrix), "--out", str(destination), "--force"],
    )

    # The bundled-directory rule never fires on a write beside it, from any
    # working directory.
    assert "must never be written there" not in result.output
    if not cwd_part:
        assert result.exit_code == 0, result.output
        assert destination.exists()
    else:
        # **The model boundary reads search paths relative to the working
        # directory** (measured on #459). From data/ or local_content/, the
        # default `local_content` search path is not this one, so the file
        # would be read as organization-internal by a command run from here.
        # The write guard matches what the boundary will say, and refuses:
        # the safe direction. #459 (b) makes the catalog carry its own licence.
        assert result.exit_code != 0, result.output
        assert "would be read as organization-internal" in result.output
        assert not destination.exists()


def test_map_builds_crosswalk_from_controls_json(tmp_path):
    from policyforge.cli import cli

    controls_path = tmp_path / "controls.json"
    _write_controls_json(
        controls_path,
        [_control(source_crosswalk={"fedramp": "IA-5 (same ID)"})],
    )
    out_path = tmp_path / "crosswalk.json"

    result = CliRunner().invoke(
        cli, ["map", "--controls", str(controls_path), "--out", str(out_path)]
    )

    assert result.exit_code == 0
    assert json.loads(out_path.read_text(encoding="utf-8")) == {"IA-5": {"fedramp": ["IA-5"]}}


def test_synthesize_writes_merged_requirements(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    controls_path = tmp_path / "controls.json"
    _write_controls_json(controls_path, [_control()])
    crosswalk_path = tmp_path / "crosswalk.json"
    crosswalk_path.write_text(json.dumps({}), encoding="utf-8")
    out_dir = tmp_path / "synthesis"

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: FakeProvider())

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Authenticator Mgmt",
            "--nist-controls",
            "IA-5",
            "--controls",
            str(controls_path),
            "--crosswalk",
            str(crosswalk_path),
            "--out-dir",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0
    written = (out_dir / "authenticator-mgmt.md").read_text(encoding="utf-8")
    assert "merged requirement [NIST IA-5]" in written


def test_synthesize_fails_when_no_controls_match(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    controls_path = tmp_path / "controls.json"
    _write_controls_json(controls_path, [_control()])
    crosswalk_path = tmp_path / "crosswalk.json"
    crosswalk_path.write_text(json.dumps({}), encoding="utf-8")

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: FakeProvider())

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Nonexistent",
            "--nist-controls",
            "ZZ-99",
            "--controls",
            str(controls_path),
            "--crosswalk",
            str(crosswalk_path),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )

    assert result.exit_code != 0


def test_generate_standard_drafts_document_from_synthesis(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    synthesis_path = tmp_path / "authenticator-mgmt.md"
    synthesis_path.write_text("- Authenticators must be managed. [NIST IA-5]\n", encoding="utf-8")
    out_path = tmp_path / "standards" / "authenticator-mgmt.md"

    fake = FakeProvider(
        text="# Authenticator Management Standard\n\n"
        "Staff must manage authenticators. [NIST IA-5]\n"
    )
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda: {"org": {"name": "Acme Corp", "industry": "Fintech", "vendors": ["Okta"]}},
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    history_dir = tmp_path / "history"

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(synthesis_path),
            "--out",
            str(out_path),
            "--history-dir",
            str(history_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Acme Corp" in fake.calls[0]["prompt"]
    assert out_path.read_text(encoding="utf-8").startswith("# Authenticator Management Standard")

    from policyforge.history.version_store import load_history

    history = load_history(history_dir, "standard/authenticator-mgmt")
    assert len(history) == 1
    assert history[0].version == 1
    assert history[0].source == "generate"


def test_generate_policy_requires_standard_and_references_its_title(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    synthesis_path = tmp_path / "authenticator-mgmt.md"
    synthesis_path.write_text("- Authenticators must be managed. [NIST IA-5]\n", encoding="utf-8")
    standard_path = tmp_path / "standards" / "authenticator-mgmt.md"
    standard_path.parent.mkdir()
    standard_path.write_text(
        "# Authenticator Management Standard\n\nStaff must manage authenticators. [NIST IA-5]\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "policies" / "authenticator-mgmt.md"

    fake = FakeProvider(
        text="# Authenticator Management Policy\n\nStaff must use strong authentication.\n"
    )
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda: {"org": {"name": "Acme Corp", "industry": "Fintech", "vendors": ["Okta"]}},
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    # --tier policy without --standard should fail fast with a clear error.
    missing_standard = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "policy",
            "--synthesis",
            str(synthesis_path),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    assert missing_standard.exit_code != 0

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "policy",
            "--synthesis",
            str(synthesis_path),
            "--standard",
            str(standard_path),
            "--out",
            str(out_path),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )

    assert result.exit_code == 0
    assert "Authenticator Management Standard" in fake.calls[0]["prompt"]
    assert out_path.read_text(encoding="utf-8").startswith("# Authenticator Management Policy")


def test_generate_parser_writes_generated_module(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    sample_path = tmp_path / "sample.csv"
    sample_path.write_text("control_id,title\nAC-1,Access Control Policy\n", encoding="utf-8")
    out_path = tmp_path / "hitrust_loader.py"

    fake = FakeProvider(
        text="from __future__ import annotations\n\n"
        "def load_hitrust_export(export_path):\n    return []\n"
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            str(sample_path),
            "--out",
            str(out_path),
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "control_id,title" in fake.calls[0]["prompt"]
    written = out_path.read_text(encoding="utf-8")
    assert "def load_hitrust_export(export_path):" in written

    # Re-running without --force should refuse to clobber the file just written.
    refused = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            str(sample_path),
            "--out",
            str(out_path),
            "--yes",
        ],
    )
    assert refused.exit_code != 0


def test_generate_parser_rejects_syntactically_invalid_output(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    sample_path = tmp_path / "sample.csv"
    sample_path.write_text("control_id,title\nAC-1,Access Control Policy\n", encoding="utf-8")
    out_path = tmp_path / "hitrust_loader.py"

    fake = FakeProvider(text="def broken(:\n    this is not python")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            str(sample_path),
            "--out",
            str(out_path),
            "--yes",
        ],
    )

    assert result.exit_code != 0
    assert not out_path.exists()


# ---- generate-parser: S-02, model-written code gated before it runs --------

#: A candidate that reads the sample and returns one record per row.
_READS_THE_SAMPLE = (
    "import csv\n\n"
    "def load_hitrust_export(export_path):\n"
    "    with open(export_path, newline='', encoding='utf-8') as handle:\n"
    "        return list(csv.DictReader(handle))\n"
)


def _generate(monkeypatch, tmp_path, source, *extra):
    import policyforge.cli as cli_mod

    sample_path = tmp_path / "sample.csv"
    sample_path.write_text("control_id,title\nAC-1,Access Control Policy\n", encoding="utf-8")
    import sys
    from pathlib import Path

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: FakeProvider(text=source))
    # Never the real package: a promotion in a test must not land in src/.
    #
    # Patched on the module that actually defines the command, found from the
    # command itself, not on `policyforge.cli` by name. A constant is read
    # from the globals of the module it lives in, so if the command ever moves
    # — the cli.py split moves every command — patching the old location
    # silently does nothing, and this test would write model-generated,
    # importable code into the real `src/policyforge/ingest/`. That is the
    # exact outcome `generate-parser` exists to prevent.
    command_module = sys.modules[cli_mod.cli.commands["generate-parser"].callback.__module__]
    monkeypatch.setattr(command_module, "_PARSER_PACKAGE_DIR", tmp_path / "package")
    real_package = Path(__file__).parents[1] / "src" / "policyforge" / "ingest"
    before = set(real_package.glob("*_loader.py"))
    candidate = tmp_path / "candidate" / "hitrust_loader.py"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            str(sample_path),
            "--out",
            str(candidate),
            "--yes",
            *extra,
        ],
    )
    # Tripwire, independent of whether the patch above reached its target.
    assert set(real_package.glob("*_loader.py")) == before, (
        "a generate-parser test wrote into the real ingest package"
    )
    return result, candidate, tmp_path / "package" / "hitrust_loader.py"


def test_the_candidate_is_written_outside_the_package_by_default():
    """Model output used to land in src/ and be importable on the next run."""
    import sys
    from pathlib import Path

    import policyforge.cli as cli_mod

    # Read from the module that defines the command, as `_generate` patches it,
    # so this keeps describing the constant the command actually uses.
    command_module = sys.modules[cli_mod.cli.commands["generate-parser"].callback.__module__]
    assert Path("output/parsers") == command_module._PARSER_CANDIDATE_DIR


def test_code_that_reaches_the_network_is_refused_and_never_run(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr("policyforge.ingest.parser_gate.trial_run", lambda *a, **k: ran.append(a))
    source = "import socket\n\ndef load_hitrust_export(export_path):\n    return []\n"
    result, candidate, target = _generate(monkeypatch, tmp_path, source)

    assert result.exit_code != 0
    assert "refused before running it" in result.output
    assert "imports 'socket'" in result.output
    assert ran == [], "a refused candidate must never reach the trial run"
    assert not candidate.exists()
    # Kept where a person can read it and judge whether the refusal was right.
    assert candidate.with_name("hitrust_loader.rejected.py").exists()
    assert not target.exists()


def test_a_write_the_static_check_cannot_see_is_caught_in_the_trial(tmp_path, monkeypatch):
    """Aliasing `open` walks past the AST check; the audit hook catches it
    at the moment it happens, and the file is never created."""
    source = (
        "def load_hitrust_export(export_path):\n"
        "    handle = open\n"
        "    handle(str(export_path) + '.leak', 'w')\n"
        "    return []\n"
    )
    result, _, target = _generate(monkeypatch, tmp_path, source)

    assert result.exit_code != 0
    assert "tried to" in result.output
    assert not (tmp_path / "sample.csv.leak").exists()
    assert not target.exists()


def test_the_trial_reports_what_it_found_and_nothing_is_promoted_unasked(tmp_path, monkeypatch):
    result, candidate, target = _generate(monkeypatch, tmp_path, _READS_THE_SAMPLE)

    assert result.exit_code == 0, result.output
    assert "1 record(s)" in result.output
    assert candidate.exists()
    assert not target.exists()
    assert "--promote" in result.output


def test_promote_installs_a_parser_that_passed_both_checks(tmp_path, monkeypatch):
    result, _, target = _generate(monkeypatch, tmp_path, _READS_THE_SAMPLE, "--promote")

    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == _READS_THE_SAMPLE


def test_a_parser_that_found_nothing_is_never_promoted(tmp_path, monkeypatch):
    """An empty catalog reads as a framework with no controls, and every
    report built on it says there is nothing to do."""
    source = "def load_hitrust_export(export_path):\n    return []\n"
    result, _, target = _generate(monkeypatch, tmp_path, source, "--promote")

    assert result.exit_code == 0, result.output
    assert "Not promoting" in result.output
    assert not target.exists()


def test_import_confluence_writes_markdown_and_records_history(tmp_path, monkeypatch):
    from policyforge.cli import cli

    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse(
            {
                "results": [
                    {
                        "id": "1",
                        "title": "Authenticator Management Standard",
                        "version": {"number": 1},
                        "body": {
                            "storage": {
                                "value": "<h1>Authenticator Management Standard</h1><p>Body</p>"
                            }
                        },
                        "_links": {"webui": "/x"},
                    }
                ]
            }
        ),
    )

    out_path = tmp_path / "imported.md"
    history_dir = tmp_path / "history"

    result = CliRunner().invoke(
        cli,
        [
            "import-confluence",
            "--tier",
            "standard",
            "--name",
            "authenticator-mgmt",
            "--space",
            "ENG",
            "--title",
            "Authenticator Management Standard",
            "--host",
            "https://example.atlassian.net/wiki",
            "--out",
            str(out_path),
            "--history-dir",
            str(history_dir),
        ],
    )

    assert result.exit_code == 0
    # The body is the page; the frontmatter records the import (#197).
    written = out_path.read_text(encoding="utf-8")
    assert written.startswith("---\nimported_from:\n"), written[:80]
    assert "\n---\n\n# Authenticator Management Standard\n" in written

    from policyforge.history.version_store import load_history

    history = load_history(history_dir, "standard/authenticator-mgmt")
    assert len(history) == 1
    assert history[0].source == "confluence-import"


def test_an_import_that_drifted_prints_a_history_command_that_runs(tmp_path, monkeypatch):
    """#310: the drift case is what the command is for, and it crashed.

    `history_hint` passed the integer version numbers to `shlex.quote`, so
    every import that differed from a recorded version exited 1. The
    helper's own test passed strings no caller sends. This runs the real
    command against a real recorded version and then runs the command it
    printed, so both halves are measured on the user's path.
    """
    import re
    import shlex
    from pathlib import Path

    import policyforge.export.confluence_importer as importer
    from policyforge.cli import cli
    from policyforge.export.confluence_importer import ConfluencePage
    from policyforge.history.version_store import record_version

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        importer,
        "fetch_confluence_page",
        lambda **_: ConfluencePage(
            id="1",
            title="Access",
            version=4,
            storage_body="<h1>Edited in Confluence</h1>",
            webui_url="/x",
        ),
    )
    record_version(
        Path("output/.history"), "standard/access", "# Drafted\n", source="generate", metadata={}
    )
    result = CliRunner().invoke(
        cli,
        [
            "import-confluence",
            "--tier",
            "standard",
            "--name",
            "access",
            "--space",
            "ENG",
            "--title",
            "Access",
            "--host",
            "https://example.atlassian.net/wiki",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Differs from the last recorded version (v1)" in result.output

    printed = re.search(r"Run `policyforge (history [^`]+)`", result.output)
    assert printed, result.output
    shown = CliRunner().invoke(cli, shlex.split(printed.group(1)))
    assert shown.exit_code == 0, shown.output
    assert "-# Drafted" in shown.output
    assert "+# Edited in Confluence" in shown.output


def test_history_command_lists_and_diffs_versions(tmp_path):
    from policyforge.cli import cli
    from policyforge.history.version_store import record_version

    history_dir = tmp_path / "history"
    record_version(history_dir, "standard/auth-mgmt", "line one\n", source="generate")
    record_version(history_dir, "standard/auth-mgmt", "line one\nline two\n", source="generate")

    listing = CliRunner().invoke(
        cli,
        ["history", "--tier", "standard", "--name", "auth-mgmt", "--history-dir", str(history_dir)],
    )
    assert listing.exit_code == 0
    assert "v1" in listing.output
    assert "v2" in listing.output

    diff = CliRunner().invoke(
        cli,
        [
            "history",
            "--tier",
            "standard",
            "--name",
            "auth-mgmt",
            "--history-dir",
            str(history_dir),
            "--diff",
            "latest",
        ],
    )
    assert diff.exit_code == 0
    assert "+line two" in diff.output


def test_history_command_reports_no_history(tmp_path):
    from policyforge.cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "history",
            "--tier",
            "standard",
            "--name",
            "never-generated",
            "--history-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "No recorded history" in result.output


def test_export_confluence_dry_run_prints_storage_format(tmp_path):
    from policyforge.cli import cli

    doc_path = tmp_path / "policy.md"
    doc_path.write_text("# Title\n\nBody text.\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "export-confluence",
            "--doc",
            str(doc_path),
            "--space",
            "ENG",
            "--title",
            "Test Page",
            "--host",
            "https://example.atlassian.net/wiki",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "<h1>Title</h1>" in result.output


def test_generate_parser_refuses_licensed_content_to_a_hosted_model(tmp_path, monkeypatch):
    """The one live boundary case, and the reason the module exists.

    A MyCSF export under local_content/ is licensed content; the configured
    provider is a hosted API. This used to be a paragraph asking the operator
    to confirm their licence and a confirm prompt they could pass --yes to.
    Now the run stops, and --yes does not get past it.
    """
    import policyforge.cli as cli_mod

    export_dir = tmp_path / "local_content"
    export_dir.mkdir()
    sample_path = export_dir / "CSFLibraryReport.csv"
    sample_path.write_text("control_id,title\n01.c,Access Control\n", encoding="utf-8")

    fake = FakeProvider(text="def load_hitrust_export(p):\n    return []\n")
    config = {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-5"},
        "frameworks": {"search_paths": [str(export_dir)]},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            str(sample_path),
            "--out",
            str(tmp_path / "hitrust_loader.py"),
            "--yes",
        ],
    )

    assert result.exit_code != 0
    assert "REFUSED" in result.output
    # Nothing was sent and nothing was written.
    assert fake.calls == []
    assert not (tmp_path / "hitrust_loader.py").exists()


def test_generate_parser_allows_licensed_content_to_a_local_model(tmp_path, monkeypatch):
    """The same export against Ollama on loopback. This is the point: the
    restriction is on the pairing, not on the content."""
    import policyforge.cli as cli_mod

    export_dir = tmp_path / "local_content"
    export_dir.mkdir()
    sample_path = export_dir / "CSFLibraryReport.csv"
    sample_path.write_text("control_id,title\n01.c,Access Control\n", encoding="utf-8")
    out_path = tmp_path / "hitrust_loader.py"

    fake = FakeProvider(text="def load_hitrust_export(p):\n    return []\n")
    config = {
        "llm": {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen3"},
        "frameworks": {"search_paths": [str(export_dir)]},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            str(sample_path),
            "--out",
            str(out_path),
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "allowed" in result.output
    assert out_path.exists()


def test_boundary_command_exits_nonzero_on_a_refused_path(tmp_path, monkeypatch):
    """So it can gate a pipeline rather than only inform a human."""
    import policyforge.cli as cli_mod

    export_dir = tmp_path / "local_content"
    export_dir.mkdir()
    export = export_dir / "export.csv"
    export.write_text("a,b\n", encoding="utf-8")

    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda: {
            "llm": {"provider": "anthropic", "model": "claude-sonnet-5"},
            "frameworks": {"search_paths": [str(export_dir)]},
        },
    )

    result = CliRunner().invoke(cli_mod.cli, ["boundary", "--path", str(export)])

    assert result.exit_code != 0
    assert "REFUSED" in result.output
    assert "licensed" in result.output


def test_generate_stamps_the_model_that_actually_wrote_the_document(tmp_path, monkeypatch):
    """The version-history stamp used to read `model` out of config.

    Config says what was configured most recently, which is a different
    question from what wrote this file — and a different answer whenever a
    cascade escalated, or whenever somebody changed the model afterwards.
    """
    import policyforge.cli as cli_mod
    from policyforge.history.version_store import load_history
    from policyforge.llm import ledger

    synthesis_path = tmp_path / "authenticator-mgmt.md"
    synthesis_path.write_text(
        "---\ntopic: Authenticator Mgmt\nowner: IAM\n---\n\n- a requirement [NIST IA-5]\n",
        encoding="utf-8",
    )
    history_dir = tmp_path / "history"
    out_path = tmp_path / "standard.md"

    class EscalatingProvider(FakeProvider):
        """Answers as the stronger half of a cascade would."""

        def generate(self, **kwargs):
            from policyforge.llm.base import LLMResponse

            self.calls.append(kwargs)
            return LLMResponse(
                text="# Authenticator Management Standard\n\nBody.",
                model="deepseek-v4-pro",
                cost_usd=0.004,
            )

    ledger_path = tmp_path / "calls.jsonl"
    config = {"llm": {"provider": "cascade", "model": "deepseek-v4-flash"}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda config: ledger.RecordingProvider(
            EscalatingProvider(),
            provider_name="cascade",
            provider_class="third-party",
            path=ledger_path,
        ),
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(synthesis_path),
            "--out",
            str(out_path),
            "--history-dir",
            str(history_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = load_history(history_dir, "standard/standard")[-1].metadata
    assert metadata["models"] == ["deepseek-v4-pro"]
    assert metadata["provider"] == "cascade"
    assert metadata["prompt_shas"]
    assert "deepseek-v4-pro" in result.output

    # And the same calls landed in the ledger, attributed to this document.
    recorded = ledger.load(ledger_path)
    assert {r.subject for r in recorded} == {"standard/standard"}
    assert {r.site for r in recorded} == {"generate"}


def test_model_log_summarizes_what_was_sent_where(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from policyforge.llm.ledger import CallRecord

    ledger_path = tmp_path / "calls.jsonl"
    ledger_path.write_text(
        "\n".join(
            record.as_json()
            for record in [
                CallRecord(
                    "2026-09-01T00:00:00+00:00",
                    "litellm",
                    "third-party",
                    "flash",
                    "standard/a",
                    "generate",
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.001,
                ),
                CallRecord(
                    "2026-09-02T00:00:00+00:00",
                    "litellm",
                    "third-party",
                    "pro",
                    "standard/a",
                    "generate",
                    input_tokens=200,
                    output_tokens=80,
                    cost_usd=0.06,
                ),
                CallRecord(
                    "2026-09-03T00:00:00+00:00",
                    "litellm",
                    "third-party",
                    "flash",
                    "standard/b",
                    "ssp",
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=0.0002,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    result = CliRunner().invoke(
        cli_mod.cli, ["model-log", "--by", "subject", "--path", str(ledger_path)]
    )

    assert result.exit_code == 0
    assert "standard/a" in result.output
    assert "standard/b" in result.output
    assert "$0.0612" in result.output

    # Filtering answers the question the record exists for: which documents
    # did this model touch.
    filtered = CliRunner().invoke(
        cli_mod.cli,
        ["model-log", "--by", "subject", "--model", "pro", "--path", str(ledger_path)],
    )
    assert "standard/a" in filtered.output
    assert "standard/b" not in filtered.output


def test_synthesize_refuses_licensed_controls_before_any_model_call(tmp_path, monkeypatch):
    """The same refusal as `ssp`, without `ssp`'s escape hatch.

    Written before the two boundary checks were merged into one helper: `ssp`'s
    refusal was tested and this one was not, so the merge could have changed
    this message — adding a `--no-narratives` hint that means nothing to
    `synthesize` — with every test still green.
    """
    import policyforge.cli as cli_mod

    catalog_dir = tmp_path / "local_content" / "hitrust"
    catalog_dir.mkdir(parents=True)
    controls_path = catalog_dir / "controls.json"
    _write_controls_json(controls_path, [_control()])
    crosswalk_path = tmp_path / "crosswalk.json"
    crosswalk_path.write_text("{}", encoding="utf-8")

    fake = FakeProvider()
    config = {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-5"},
        "frameworks": {"search_paths": [str(tmp_path / "local_content")]},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Authenticator Mgmt",
            "--nist-controls",
            "IA-5",
            "--controls",
            str(controls_path),
            "--crosswalk",
            str(crosswalk_path),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )

    assert result.exit_code != 0
    assert "REFUSED" in result.output
    assert "--no-narratives" not in result.output
    assert fake.calls == []


def test_ssp_refuses_licensed_controls_when_it_would_draft_narratives(tmp_path, monkeypatch):
    """The highest-volume model path in the project.

    `ssp` takes `--controls` and drafts one narrative per control from the
    control text itself, so an unguarded run against a licensed GovRAMP or
    HITRUST catalog sends several hundred requests of it rather than one.
    """
    import policyforge.cli as cli_mod

    catalog_dir = tmp_path / "local_content" / "govramp"
    catalog_dir.mkdir(parents=True)
    controls_path = catalog_dir / "controls.json"
    _write_controls_json(controls_path, [_control()])

    fake = FakeProvider()
    config = {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-5"},
        "frameworks": {"search_paths": [str(tmp_path / "local_content")]},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["ssp", "--controls", str(controls_path), "--out", str(tmp_path / "ssp.xlsx"), "--yes"],
    )

    assert result.exit_code != 0
    assert "REFUSED" in result.output
    # The refusal names the zero-call escape hatch, which for this command is
    # a real answer rather than a workaround.
    assert "--no-narratives" in result.output
    assert fake.calls == []


def test_ssp_builds_the_workbook_from_licensed_controls_without_narratives(tmp_path, monkeypatch):
    """No model call, so nothing leaves and nothing is refused.

    The boundary is about what reaches a provider, not about what may be read
    off disk — `--no-narratives` was always the zero-call option and stays a
    complete answer for a licensed catalog.
    """
    import policyforge.cli as cli_mod

    catalog_dir = tmp_path / "local_content" / "govramp"
    catalog_dir.mkdir(parents=True)
    controls_path = catalog_dir / "controls.json"
    _write_controls_json(controls_path, [_control()])
    out_path = tmp_path / "ssp.xlsx"

    fake = FakeProvider()
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda: {
            "llm": {"provider": "anthropic", "model": "claude-sonnet-5"},
            "frameworks": {"search_paths": [str(tmp_path / "local_content")]},
        },
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["ssp", "--controls", str(controls_path), "--out", str(out_path), "--no-narratives"],
    )

    assert result.exit_code == 0, result.output
    assert out_path.exists()
    assert fake.calls == []

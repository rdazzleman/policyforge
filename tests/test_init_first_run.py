"""First run: `init` writes a working project, `--refresh` updates one, and the
expected failures are one line each (#176 part 2, 80's spec).

A user who has only run `pip install` or `brew install` gets: a starter
`config/config.yaml` (their provider and model, or a local model needing no
key, with the catalog set the starter registry needs), a `config/topics.yaml`
to cut down, and after an upgrade `init --refresh` says what changed. A
missing `llm:` block, a missing registry, a missing Confluence token and an
HTTP failure each say what is wrong and what to do, in one line.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import requests
import yaml
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.scaffold import STARTER_CATALOGS

PHI_TOPICS = ("HIPAA Privacy Practices", "Breach Notification")

REPO = Path(__file__).resolve().parent.parent

#: These tests run commands against the `config/config.yaml` that `init` wrote
#: into a temporary project, so they read the config they built (#26).
pytestmark = pytest.mark.own_config


@pytest.fixture(autouse=True)
def _in_the_project_directory(tmp_path, monkeypatch):
    """Every test runs in the temporary project, never in the checkout."""
    monkeypatch.chdir(tmp_path)


def _init(directory: Path, *args: str):
    return CliRunner().invoke(cli_mod.cli, ["init", str(directory), *args])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- init writes config.yaml and topics.yaml ------------------------------------


def test_init_writes_a_local_model_starter_config_and_the_registry(tmp_path):
    result = _init(tmp_path)
    assert result.exit_code == 0, result.output

    config = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert config["llm"] == {
        "provider": "local",
        "model": "qwen3:14b",
        "base_url": "http://localhost:11434/v1",
    }
    assert config["programme"]["catalogs"] == list(STARTER_CATALOGS)
    # The registry is built from the scope facts (22, part 3): nothing declared
    # gives the 16 core topics, and the example stays beside it as the pool.
    registry = yaml.safe_load((tmp_path / "config" / "topics.yaml").read_text(encoding="utf-8"))
    assert len(registry["topics"]) == 16
    assert (tmp_path / "config" / "topics.example.yaml").is_file()


def test_the_starter_config_is_classified_local_so_licensed_content_may_use_it(tmp_path):
    from policyforge.llm.boundary import classify_provider

    _init(tmp_path)
    config = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert classify_provider(config["llm"]).klass == "local"


def test_the_first_command_works_with_no_flags(tmp_path, monkeypatch):
    """The review that filed 176 measured the starter registry's AI topics
    reading as missing, and `coverage` failing on a missing `--controls`. With
    the starter config both are answered by the profile `init` wrote."""
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli_mod.cli, ["coverage"])

    assert result.exit_code == 0, result.output
    assert f"catalogs: programme (5): {', '.join(STARTER_CATALOGS)}" in result.stderr
    assert "don't exist in the catalog" not in result.output
    assert "Unknown anchors" not in result.output


def test_a_chosen_provider_and_the_name_of_its_key_variable_are_written(tmp_path):
    result = _init(tmp_path, "--provider", "anthropic", "--api-key-env", "MY_ANTHROPIC_KEY")
    assert result.exit_code == 0, result.output

    text = (tmp_path / "config" / "config.yaml").read_text(encoding="utf-8")
    llm = yaml.safe_load(text)["llm"]
    assert llm == {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "api_key_env": "MY_ANTHROPIC_KEY",
    }
    assert "export MY_ANTHROPIC_KEY=..." in result.output
    assert "sk-" not in text, "a key is never asked for or written"


def test_a_provider_with_no_starter_needs_a_model(tmp_path):
    result = _init(tmp_path, "--provider", "bedrock")
    assert result.exit_code == 2, result.output
    assert "--model" in result.output
    assert not (tmp_path / "config" / "config.yaml").exists()

    ok = _init(tmp_path, "--provider", "bedrock", "--model", "my-model-id")
    assert ok.exit_code == 0, ok.output
    llm = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))["llm"]
    assert llm == {"provider": "bedrock", "model": "my-model-id"}


def test_init_never_overwrites_config_or_the_registry(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("llm: {provider: mine}\n", encoding="utf-8")
    (tmp_path / "config" / "topics.yaml").write_text("topics: []\n", encoding="utf-8")

    result = _init(tmp_path, "--provider", "anthropic")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "config" / "config.yaml").read_text(encoding="utf-8") == (
        "llm: {provider: mine}\n"
    )
    assert (tmp_path / "config" / "topics.yaml").read_text(encoding="utf-8") == "topics: []\n"
    assert "kept   config/config.yaml" in result.output


# ---- init --refresh ----------------------------------------------------------------


def test_refresh_replaces_what_changed_says_so_and_keeps_the_old_copy(tmp_path):
    _init(tmp_path)
    catalog = tmp_path / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"
    packaged = _sha(catalog)
    catalog.write_text('[{"an": "older export"}]', encoding="utf-8")
    old = _sha(catalog)

    result = _init(tmp_path, "--refresh")

    assert result.exit_code == 0, result.output
    assert _sha(catalog) == packaged
    name = "data/frameworks/nist-800-53-r5/controls.json"
    line = f"updated  {name} (sha256 {old[:8]} -> {packaged[:8]})"
    assert line in result.output
    assert "1 updated, 0 added" in result.output
    backups = list((tmp_path / "output" / ".init-backup").rglob("controls.json"))
    assert len(backups) == 1 and _sha(backups[0]) == old
    assert "output/.init-backup/" in result.output


def test_refresh_adds_a_file_this_version_ships_and_the_project_lacks(tmp_path):
    _init(tmp_path)
    missing = tmp_path / "data" / "frameworks" / "nist-csf-2-0" / "controls.json"
    missing.unlink()

    result = _init(tmp_path, "--refresh")

    assert missing.exists()
    assert "added    data/frameworks/nist-csf-2-0/controls.json" in result.output
    assert "0 updated, 1 added" in result.output


def test_refresh_with_nothing_to_do_changes_nothing_and_says_so(tmp_path):
    _init(tmp_path)
    result = _init(tmp_path, "--refresh")
    assert result.exit_code == 0, result.output
    assert "0 updated, 0 added" in result.output
    assert not (tmp_path / "output" / ".init-backup").exists()


def test_refresh_never_touches_the_projects_own_files(tmp_path):
    _init(tmp_path)
    own = {
        tmp_path / "config" / "config.yaml": "llm: {provider: mine, model: m}\n",
        tmp_path / "config" / "topics.yaml": "topics: [mine]\n",
        tmp_path / ".gitignore": "mine\n",
    }
    for path, text in own.items():
        path.write_text(text, encoding="utf-8")
    (tmp_path / "local_content" / "licensed.json").write_text("{}", encoding="utf-8")

    result = _init(tmp_path, "--refresh")

    assert result.exit_code == 0, result.output
    for path, text in own.items():
        assert path.read_text(encoding="utf-8") == text
    assert (tmp_path / "local_content" / "licensed.json").read_text(encoding="utf-8") == "{}"


def test_refresh_where_there_is_no_project_says_to_run_init(tmp_path):
    result = _init(tmp_path, "--refresh")
    assert result.exit_code == 1, result.output
    assert "policyforge init" in result.output
    assert not (tmp_path / "data").exists()


def test_refresh_does_not_take_the_config_options(tmp_path):
    result = _init(tmp_path, "--refresh", "--provider", "anthropic")
    assert result.exit_code == 2, result.output
    assert "--refresh" in result.output


# ---- expected failures, one line each ----------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _one_error_line(result) -> str:
    """The message of a refusal: a `ClickException` or `UsageError`, never a
    traceback from inside the tool."""
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    lines = [line for line in result.output.splitlines() if line.startswith("Error:")]
    assert len(lines) == 1, result.output
    return lines[0]


@pytest.mark.parametrize("config", [{}, {"llm": None}, {"llm": {}}, {"llm": {"model": "m"}}])
def test_a_config_with_no_llm_block_says_so_in_one_line(project, monkeypatch, config):
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)

    result = CliRunner().invoke(cli_mod.cli, ["llm-check"])

    assert result.exit_code == 2, result.output
    line = _one_error_line(result)
    assert "`llm:`" in line and "policyforge init" in line and "config.example.yaml" in line


def test_a_missing_registry_says_what_to_do_next(project):
    result = CliRunner().invoke(cli_mod.cli, ["coverage", "--topics", "nowhere/topics.yaml"])

    assert result.exit_code == 2, result.output
    line = _one_error_line(result)
    assert "No topic registry at nowhere" in line and "policyforge init" in line


def test_a_missing_confluence_token_is_one_line_naming_the_variable(project, monkeypatch):
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    monkeypatch.delenv("CONFLUENCE_USERNAME", raising=False)
    (project / "docs" / "standards").mkdir(parents=True)
    (project / "docs" / "standards" / "a.md").write_text(
        "---\nconfluence:\n  space: SEC\n  title: A Page\n---\n\n# A Standard\n\nText.\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli_mod.cli, ["publish", "--host", "https://wiki.invalid/wiki", "--apply"]
    )

    assert result.exit_code == 2, result.output
    line = _one_error_line(result)
    assert "CONFLUENCE_API_TOKEN" in line and "is not set" in line


def test_the_missing_credential_is_still_a_runtime_error_to_callers_that_catch_one():
    from policyforge.config import ConfigError, MissingCredentialError

    assert issubclass(MissingCredentialError, RuntimeError)
    assert issubclass(MissingCredentialError, ConfigError)


@pytest.mark.parametrize(
    ("status", "reason", "expect"),
    [
        (401, "Unauthorized", "username and the API token"),
        (403, "Forbidden", "username and the API token"),
        (404, "Not Found", "space and title"),
        (503, "Service Unavailable", "try again later"),
    ],
)
def test_an_http_status_failure_is_one_line_with_what_to_check(
    project, monkeypatch, status, reason, expect
):
    response = requests.Response()
    response.status_code = status
    response.reason = reason
    response.url = "https://alice:s3cret@wiki.example.com/wiki/rest/api/content?token=abc123"

    def refuse(*_, **__):
        raise requests.exceptions.HTTPError(f"{status} {reason}", response=response)

    # The real `fetch_confluence_page` runs: it is what tags the failure as
    # Confluence's, and the request inside it is the one that fails (#413).
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "a-token")
    monkeypatch.setattr(requests, "get", refuse)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "import-confluence",
            "--tier",
            "standard",
            "--name",
            "a-page",
            "--space",
            "SEC",
            "--title",
            "A Page",
            "--host",
            "https://wiki.example.com/wiki",
        ],
    )

    assert result.exit_code == 1, result.output
    line = _one_error_line(result)
    assert line.startswith(f"Error: {status} {reason} from https://wiki.example.com/wiki/rest")
    assert expect in line
    assert "s3cret" not in result.output and "abc123" not in result.output


def test_an_unreachable_host_is_one_line_naming_it(project, monkeypatch):
    request = requests.Request("GET", "https://wiki.invalid/wiki/rest/api/content").prepare()

    def refuse(*_, **__):
        raise requests.exceptions.ConnectionError("dns failure", request=request)

    # The real `fetch_confluence_page` runs: it is what tags the failure as
    # Confluence's, and the request inside it is the one that fails (#413).
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "a-token")
    monkeypatch.setattr(requests, "get", refuse)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "import-confluence",
            "--tier",
            "standard",
            "--name",
            "a-page",
            "--space",
            "SEC",
            "--title",
            "A Page",
            "--host",
            "https://wiki.invalid/wiki",
        ],
    )

    assert result.exit_code == 1, result.output
    line = _one_error_line(result)
    assert "Could not reach https://wiki.invalid/wiki/rest/api/content" in line
    assert "ConnectionError" in line


def test_each_starter_matches_its_block_in_the_example_config():
    """`init` writes the figures `config.example.yaml` shows for each provider:
    a starter that drifted from the example would be a config nobody
    documented. The example's blocks are read the way a user would copy them."""
    from policyforge.scaffold import LLM_STARTERS

    text = (REPO / "config" / "config.example.yaml").read_text(encoding="utf-8")
    blocks: dict[str, dict] = {}
    current: list[str] = []
    for line in [*text.splitlines(), ""]:
        stripped = line[2:] if line.startswith("# ") else line
        if line == "llm:" or line == "# llm:":
            current = ["llm:"]
        elif current and (line.startswith("  ") or line.startswith("#   ")):
            current.append(stripped if line.startswith("  ") else line[2:])
        elif current:
            block = yaml.safe_load("\n".join(current))["llm"]
            if isinstance(block, dict) and "provider" in block:
                blocks.setdefault(block["provider"], block)
            current = []

    for provider, starter in LLM_STARTERS.items():
        example = blocks[provider]
        assert starter == {k: v for k, v in example.items() if k in starter or k == "provider"}, (
            provider
        )


def test_the_first_coverage_counts_all_three_hipaa_rules_and_names_what_a_court_vacated(
    tmp_path, monkeypatch
):
    """80's ruling on #420: the starter's two PHI topics anchor the Privacy and
    Breach Notification Rules' sections, and the Security Rule rides along as
    one regulation. Every paragraph under an anchored section is counted, and
    the paragraphs Purl v. HHS vacated are named, not counted."""
    import json

    _init(tmp_path, "--personal-data", "phi")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli_mod.cli, ["coverage", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    in_scope = set(report["in_scope"])
    assert {"164.502", "164.502(b)", "164.502(b)(2)(iii)", "164.404", "164.404(a)(1)"} <= in_scope
    assert report["unknown_anchors"] == {}
    assert report["out_of_scope_anchors"] == {}, "the starter anchors nothing outside its scope"
    assert report["vacated_anchors"] == {}, "the starter anchors no text a court vacated"
    vacated = report["vacated"]["HIPAA Privacy Rule"]
    assert vacated["judgment"] == "Purl v. HHS"
    assert vacated["ids"] and not set(vacated["ids"]) & in_scope
    assert (
        "HIPAA reachable via the crosswalk" in CliRunner().invoke(cli_mod.cli, ["coverage"]).output
    )


def test_deleting_the_phi_topics_and_their_catalogs_leaves_no_hipaa_in_the_numbers(
    tmp_path, monkeypatch
):
    import json

    _init(tmp_path, "--personal-data", "phi")
    monkeypatch.chdir(tmp_path)
    topics = yaml.safe_load((tmp_path / "config" / "topics.yaml").read_text(encoding="utf-8"))
    assert {t["name"] for t in topics["topics"]} >= set(PHI_TOPICS)
    topics["topics"] = [t for t in topics["topics"] if t["name"] not in PHI_TOPICS]
    (tmp_path / "config" / "topics.yaml").write_text(yaml.safe_dump(topics), encoding="utf-8")
    config_path = tmp_path / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["programme"]["catalogs"] = [
        c for c in config["programme"]["catalogs"] if "hipaa" not in c
    ]
    # An organisation that drops its PHI topics says so: with `personal_data:
    # phi` still declared, `coverage` rightly names them as missing (22, part 3).
    config["programme"]["personal_data"] = "none"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = CliRunner().invoke(cli_mod.cli, ["coverage", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert not [i for i in report["in_scope"] if i.startswith("164.")]
    assert report["vacated"] == {}
    assert "HIPAA" not in result.stdout


def test_init_help_names_every_catalog_it_copies():
    """1d on #420: the help said "NIST 800-53, FedRAMP, ARC-AMPE, the HIPAA
    Security Rule" while thirteen are copied. Keyed by directory, by hand, so a
    catalog added to `BUNDLED_CATALOGS` fails here until the help names it."""
    from policyforge.scaffold import BUNDLED_CATALOGS

    named = {
        "arc-ampe": "ARC-AMPE",
        "cfr-170-315-onc-certification": "ONC 170.315",
        "cfr-171-information-blocking": "Information Blocking",
        "cfr-42-part-2-sud-records": "42 CFR Part 2",
        "fedramp": "FedRAMP",
        "hipaa-breach-notification-rule": "Breach Notification Rule",
        "hipaa-privacy-rule": "Privacy Rule",
        "hipaa-security-rule": "HIPAA Security Rule",
        "nist-800-171-r3": "800-171",
        "nist-800-53-r5": "NIST 800-53",
        "nist-ai-rmf": "AI RMF",
        "nist-ai-rmf-playbook": "Playbook",
        "nist-csf-2-0": "CSF 2.0",
    }
    assert set(named) == set(BUNDLED_CATALOGS)

    result = CliRunner().invoke(cli_mod.cli, ["init", "--help"])

    text = " ".join(result.output.split())
    assert [phrase for phrase in named.values() if phrase not in text] == []

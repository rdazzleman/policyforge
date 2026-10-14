"""Policy grouping, part 2a (22; 80's spec in comment 5919, amended by 5925).

One Policy covers one or more Standards, and each Standard (a topic) names
exactly one with `policy:`. Registries are hand-written here: the expected
membership never comes from the code that reads them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.content.check import check_tree
from policyforge.llm.base import LLMResponse
from policyforge.synthesis.merge import read_synthesis, write_synthesis
from policyforge.topics.coverage import analyze_coverage, format_report
from policyforge.topics.registry import (
    Registry,
    TopicRegistryError,
    load_registry,
    load_topics,
    parse_registry,
)

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / "config" / "topics.example.yaml"


def _registry(text: str) -> Registry:
    return parse_registry(yaml.safe_load(text))


TWO_POLICIES = """
policies:
  - name: Access Control
    owner: CISO
  - name: Continuity
    owner: COO
topics:
  - name: Identity Lifecycle
    owner: IAM
    policy: Access Control
    nist_controls: [AC-2]
  - name: Authentication
    owner: IAM
    policy: Access Control
    nist_controls: [IA-5]
  - name: Backup
    owner: Infrastructure
    policy: Continuity
    nist_controls: [CP-9]
"""


# ---- the registry -------------------------------------------------------------


def test_a_topic_names_its_policy_and_the_block_declares_it():
    registry = _registry(TWO_POLICIES)

    assert [(p.name, p.owner) for p in registry.policies] == [
        ("Access Control", "CISO"),
        ("Continuity", "COO"),
    ]
    assert [t.name for t in registry.standards_of("Access Control")] == [
        "Identity Lifecycle",
        "Authentication",
    ]
    assert [t.name for t in registry.standards_of(" continuity ")] == ["Backup"]
    assert registry.undeclared_policies() == {}


def test_policy_names_match_without_regard_to_case_and_padding(tmp_path):
    registry = _registry(
        "policies:\n  - {name: Access Control, owner: X}\n"
        "topics:\n  - {name: T, owner: O, policy: '  access control ', nist_controls: [AC-2]}\n"
    )

    assert registry.undeclared_policies() == {}
    report = check_tree(_tree(tmp_path, t="ACCESS CONTROL"), registry=registry)
    assert [m for m in _messages(report, "error") if "olicy" in m] == []


def test_a_registry_with_no_policies_block_still_loads_and_names_what_is_missing():
    registry = _registry(
        "topics:\n  - name: A\n    owner: O\n    policy: Ghost\n    nist_controls: [AC-2]\n"
        "  - name: B\n    owner: O\n    nist_controls: [AC-3]\n"
    )

    assert registry.policies == []
    assert registry.undeclared_policies() == {"Ghost": ["A"]}
    assert [t.policy for t in registry.topics] == ["Ghost", ""]


def test_load_topics_is_unchanged_for_its_callers(tmp_path):
    path = tmp_path / "topics.yaml"
    path.write_text(TWO_POLICIES, encoding="utf-8")

    assert [t.name for t in load_topics(path)] == [t.name for t in load_registry(path).topics]
    assert load_topics(path)[0].policy == "Access Control"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("policies: {a: b}\ntopics: [{name: T, owner: O}]", "must be a list"),
        ("policies: [x]\ntopics: [{name: T, owner: O}]", "Policy #1 is not a mapping"),
        ("policies: [{name: P}]\ntopics: [{name: T, owner: O}]", "missing `owner`"),
        ("policies: [{owner: X}]\ntopics: [{name: T, owner: O}]", "missing `name`"),
        (
            "policies: [{name: P, owner: X, colour: red}]\ntopics: [{name: T, owner: O}]",
            "unknown key\\(s\\): colour",
        ),
        (
            "policies: [{name: P, owner: X}, {name: p, owner: Y}]\ntopics: [{name: T, owner: O}]",
            "Duplicate Policy name",
        ),
    ],
)
def test_a_malformed_policies_block_is_refused_by_name(text, message):
    with pytest.raises(TopicRegistryError, match=message):
        _registry(text)


def test_the_starter_groups_every_topic_under_a_declared_policy():
    """Hand-written from 5b's grouping (5925) with the AI block kept apart
    (5919) and the two PHI topics under Data Protection & Privacy (420)."""
    registry = load_registry(STARTER)

    expected = {
        "Governance & Risk": {"Security Program Governance", "Risk Assessment & Authorization"},
        "Access Control": {
            "Identity Lifecycle & Access Review",
            "Authentication & Credential Management",
            "Remote & Third-Party Access",
        },
        "Network & Cryptography": {"Network & Boundary Protection", "Encryption & Key Management"},
        "Operations Security": {
            "Change & Configuration Management",
            "Asset Management",
            "System Maintenance",
            "Vulnerability & Patch Management",
            "Endpoint Protection & Device Controls",
        },
        "Monitoring & Incident": {"Logging, Monitoring & Alerting", "Incident Response"},
        "Continuity": {"Backup, Restore & Contingency"},
        "Data Protection & Privacy": {
            "Data Protection",
            "Privacy",
            "Media Handling & Disposal",
            "HIPAA Privacy Practices",
            "Breach Notification",
        },
        "Third-Party Risk": {"Business Associate & Vendor Risk"},
        "People": {"Workforce Security Training", "Personnel Security"},
        "Physical": {"Physical & Environmental Security"},
        "Secure Development": {"Secure Development Lifecycle"},
        "AI Governance": {
            "AI Governance & Accountability",
            "AI System Inventory & Impact Assessment",
            "AI Evaluation & Measurement",
            "AI Risk Treatment & Incident Response",
            "Third-Party AI & Data Supply Chain",
            "Responsible Use of AI",
            "Data for AI Systems",
            "Transparency to Affected People",
        },
    }
    actual = {p.name: {t.name for t in registry.standards_of(p.name)} for p in registry.policies}

    assert actual == expected
    assert registry.undeclared_policies() == {}
    assert sum(len(v) for v in expected.values()) == len(registry.topics) == 33
    # The user's ask: about 1/2 to 2/3 as many Policies as Standards.
    assert len(registry.policies) <= 0.6 * len(registry.topics)


def test_two_policies_in_the_starter_do_not_share_a_page():
    registry = load_registry(STARTER)

    pages = [p.page("confluence") for p in registry.policies if p.page("confluence")]

    assert len(pages) == len(set(pages))


# ---- check --------------------------------------------------------------------


def _tree(tmp_path: Path, **standards: str) -> Path:
    root = tmp_path / "docs"
    (root / "standards").mkdir(parents=True)
    for name, policy in standards.items():
        front = f"policy: {policy}\n" if policy else ""
        (root / "standards" / f"{name}.md").write_text(
            f"---\ntitle: {name}\ntier: standard\nowner: IAM\n{front}---\n\n# {name}\n\nText.\n",
            encoding="utf-8",
        )
    return root


def _messages(report, severity=None):
    return [f.message for f in report.findings if severity in (None, f.severity)]


def test_a_standard_naming_an_undeclared_policy_is_an_error(tmp_path):
    registry = _registry(TWO_POLICIES)
    root = _tree(tmp_path, access="Access Control", orphan="Ghost Policy")

    report = check_tree(root, registry=registry, registry_path="config/topics.yaml")

    errors = _messages(report, "error")
    assert len(errors) == 1
    assert "'Ghost Policy'" in errors[0] and "doesn't declare" in errors[0]
    assert not report.ok


def test_a_declared_policy_no_standard_names_is_a_warning(tmp_path):
    registry = _registry(
        "policies:\n  - {name: Empty, owner: X}\n  - {name: Used, owner: Y}\n"
        "topics:\n  - {name: T, owner: O, policy: Used, nist_controls: [AC-2]}\n"
    )

    report = check_tree(_tree(tmp_path), registry=registry)

    assert _messages(report, "warning") == [
        "Policy 'Empty' is declared but no topic or Standard names it"
    ]
    assert report.ok


def test_a_policy_named_only_by_a_standards_frontmatter_is_not_empty(tmp_path):
    registry = _registry(
        "policies:\n  - {name: Lone, owner: X}\n"
        "topics:\n  - {name: T, owner: O, nist_controls: [AC-2]}\n"
    )

    report = check_tree(_tree(tmp_path, t="Lone"), registry=registry)

    assert not any("Lone" in m and "no topic" in m for m in _messages(report))


def test_a_topic_with_no_policy_is_a_warning_that_says_it_is_its_own(tmp_path):
    registry = _registry("topics:\n  - {name: Solo, owner: O, nist_controls: [AC-2]}\n")

    report = check_tree(_tree(tmp_path), registry=registry)

    assert _messages(report, "warning") == [
        "topic 'Solo' names no `policy:`, so it is its own Policy, named after the topic"
    ]


def test_two_policies_claiming_one_page_is_an_error(tmp_path):
    registry = _registry(
        "policies:\n"
        "  - {name: A, owner: X, confluence: {space: SEC, pages: {policy: Security Policy}}}\n"
        "  - {name: B, owner: Y, confluence: {space: sec, pages: {policy: security policy}}}\n"
        "  - {name: C, owner: Z, confluence: {space: OPS, pages: {policy: Security Policy}}}\n"
        "topics:\n"
        "  - {name: T1, owner: O, policy: A, nist_controls: [AC-2]}\n"
        "  - {name: T2, owner: O, policy: B, nist_controls: [AC-3]}\n"
        "  - {name: T3, owner: O, policy: C, nist_controls: [AC-4]}\n"
    )

    report = check_tree(_tree(tmp_path), registry=registry)

    errors = _messages(report, "error")
    assert errors == [
        "Policy 'B' publishes to sec/'security policy', which Policy 'A' already claims — "
        "one of them would silently overwrite the other"
    ]


def test_no_registry_means_no_policy_findings(tmp_path):
    report = check_tree(_tree(tmp_path, access="Anything"))

    assert [m for m in _messages(report) if "olicy" in m] == []


def test_the_check_command_reads_the_registry_and_fails_on_an_undeclared_policy(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    root = _tree(tmp_path, orphan="Ghost Policy")
    topics = tmp_path / "topics.yaml"
    topics.write_text(TWO_POLICIES, encoding="utf-8")

    result = CliRunner().invoke(
        cli_mod.cli, ["check", "--content-dir", str(root), "--topics", str(topics)]
    )

    assert result.exit_code == 1, result.output
    assert "'Ghost Policy'" in result.output and "topics.yaml" in result.output


def test_the_check_command_passes_a_grouped_standard_and_skips_a_missing_registry(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    root = _tree(tmp_path, access="Access Control")
    topics = tmp_path / "topics.yaml"
    topics.write_text(TWO_POLICIES, encoding="utf-8")

    grouped = CliRunner().invoke(
        cli_mod.cli, ["check", "--content-dir", str(root), "--topics", str(topics)]
    )
    absent = CliRunner().invoke(cli_mod.cli, ["check", "--content-dir", str(root)])

    assert grouped.exit_code == 0, grouped.output
    assert "Policy" not in absent.output and absent.exit_code == 0, absent.output


def test_the_check_command_refuses_a_topics_path_that_is_not_there(tmp_path):
    result = CliRunner().invoke(
        cli_mod.cli,
        ["check", "--content-dir", str(_tree(tmp_path)), "--topics", str(tmp_path / "nope.yaml")],
    )

    assert result.exit_code == 2 and "doesn't exist" in result.output


def test_a_malformed_registry_is_a_finding_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    topics = tmp_path / "topics.yaml"
    topics.write_text("policies: [{name: P}]\ntopics: [{name: T, owner: O}]\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli_mod.cli, ["check", "--content-dir", str(_tree(tmp_path)), "--topics", str(topics)]
    )

    assert result.exit_code == 1 and "missing `owner`" in result.output


def test_a_run_of_the_starter_registry_with_every_standard_grouped_checks_clean(tmp_path):
    """80's done-when: `check` passes the grouping applied. One Standard per
    starter topic, each carrying the `policy:` the registry gives it."""
    registry = load_registry(STARTER)
    root = tmp_path / "docs"
    (root / "standards").mkdir(parents=True)
    for index, topic in enumerate(registry.topics):
        (root / "standards" / f"s{index}.md").write_text(
            f"---\ntitle: S{index}\ntier: standard\nowner: {topic.owner}\n"
            f"policy: {topic.policy}\n---\n\n# S{index}\n\nText.\n",
            encoding="utf-8",
        )

    report = check_tree(root, registry=registry)

    assert report.errors == []
    assert not any("is declared but" in m or "names no `policy" in m for m in _messages(report))


# ---- coverage -----------------------------------------------------------------


def test_coverage_names_a_policy_a_topic_uses_and_the_block_does_not_declare():
    registry = _registry(
        "topics:\n  - {name: A, owner: O, policy: Ghost, nist_controls: [AC-2]}\n"
        "  - {name: B, owner: O, policy: Declared, nist_controls: [AC-3]}\n"
    )

    report = analyze_coverage(registry.topics, [], declared_policies={"declared"})

    assert report.undeclared_policies == {"Ghost": ["A"]}
    assert "Policies a topic names but `policies:` doesn't declare" in format_report(report)
    assert "Ghost: A" in format_report(report)


def test_coverage_asked_nothing_about_policies_says_nothing():
    registry = _registry("topics:\n  - {name: A, owner: O, policy: Ghost, nist_controls: [AC-2]}\n")

    report = analyze_coverage(registry.topics, [])

    assert report.undeclared_policies == {}
    assert "doesn't declare" not in format_report(report)


def test_the_coverage_command_reports_an_undeclared_policy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    topics = tmp_path / "topics.yaml"
    topics.write_text(
        "topics:\n  - {name: A, owner: O, policy: Ghost, nist_controls: [AC-2]}\n",
        encoding="utf-8",
    )
    controls = ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"

    result = CliRunner().invoke(
        cli_mod.cli, ["coverage", "--topics", str(topics), "--controls", str(controls), "--json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["undeclared_policies"] == {"Ghost": ["A"]}


# ---- the Standard says which Policy it belongs to ------------------------------


def test_the_synthesis_carries_the_parent_policy_to_generate():
    text = write_synthesis("- a requirement\n", topic="T", owner="O", policy="Access Control")

    metadata, _ = read_synthesis(text)

    assert metadata["policy"] == "Access Control"
    assert "policy" not in read_synthesis(write_synthesis("- r\n", topic="T", owner="O"))[0]


def test_a_standard_names_its_parent_policy_in_one_sentence():
    from policyforge.generate.policy_writer import with_parent_policy

    text = "# Access Review Standard\n\n## Purpose\n\nWhy.\n"

    assert with_parent_policy(text, "Access Control") == (
        "# Access Review Standard\n\nThis Standard implements the Access Control Policy.\n\n"
        "## Purpose\n\nWhy.\n",
        True,
    )
    assert (
        "implements the Information Security Policy."
        in with_parent_policy(text, "Information Security Policy")[0]
    )
    assert (
        "implements the Policy on Remote Work."
        in with_parent_policy(text, "Policy on Remote Work")[0]
    )
    assert "This Standard has no parent Policy." in with_parent_policy(text, "")[0]


SENTENCE = "This Standard implements the Access Control Policy."


@pytest.mark.parametrize(
    ("label", "text", "expected"),
    [
        (
            "setext title",
            "Access Review Standard\n======================\n\n## Purpose\n\nWhy.\n",
            "Access Review Standard\n======================\n\n" + SENTENCE + "\n\n"
            "## Purpose\n\nWhy.\n",
        ),
        (
            "a fenced line that looks like a title comes first",
            "```text\n# not a title\n```\n\n# Access Review Standard\n\n## Purpose\n\nWhy.\n",
            "```text\n# not a title\n```\n\n# Access Review Standard\n\n" + SENTENCE + "\n\n"
            "## Purpose\n\nWhy.\n",
        ),
        (
            "a tilde fence",
            "~~~\n# not a title\n~~~\n# Access Review Standard\n\nWhy.\n",
            "~~~\n# not a title\n~~~\n# Access Review Standard\n\n" + SENTENCE + "\n\nWhy.\n",
        ),
        (
            "no blank line after the title",
            "# Access Review Standard\n## Purpose\n\nWhy.\n",
            "# Access Review Standard\n\n" + SENTENCE + "\n\n## Purpose\n\nWhy.\n",
        ),
    ],
)
def test_the_sentence_follows_the_real_title(label, text, expected):
    from policyforge.generate.policy_writer import with_parent_policy

    assert with_parent_policy(text, "Access Control") == (expected, True), label


@pytest.mark.parametrize(
    "text",
    [
        "## Access Review Standard\n\n## Purpose\n\nWhy.\n",
        "Access Review Standard\n----------------------\n\nWhy.\n",
        "```text\n# only in a fence\n```\n\nWhy.\n",
        "no heading at all\n",
        "## Title as an H2\n\n# A later H1\n\nWhy.\n",
    ],
)
def test_a_draft_with_no_level_one_title_gets_the_sentence_first_and_says_so(text):
    from policyforge.generate.policy_writer import with_parent_policy

    result, titled = with_parent_policy(text, "Access Control")

    assert titled is False
    assert result == SENTENCE + "\n\n" + text


def test_generate_says_so_when_the_draft_has_no_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    synthesis = tmp_path / "access-review.md"
    synthesis.write_text(
        "---\ntopic: Access Review\nowner: IAM\npolicy: Access Control\n---\n\n- R. [NIST AC-2]\n",
        encoding="utf-8",
    )
    config = {"org": {"name": "Acme", "industry": "H"}, "zardoz": {"host": "https://w.example"}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda _cfg: _Fake(
            "## Access Review Standard\n\n## Purpose\n\nReview access. [NIST AC-2]\n"
        ),
    )

    result = CliRunner().invoke(
        cli_mod.cli, ["generate", "--tier", "standard", "--synthesis", str(synthesis)]
    )

    assert result.exit_code == 0, result.output
    assert "has no '# ' title, so the parent-Policy sentence is its first line" in result.output
    text = (tmp_path / "docs" / "standards" / "access-review.md").read_text(encoding="utf-8")
    assert SENTENCE in text


class _Fake:
    def __init__(self, text):
        self.text = text

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def _generate_standard(tmp_path, monkeypatch, synthesis_policy):
    return _generate_standard_with(tmp_path, monkeypatch, synthesis_policy)[0]


def _generate_standard_with(tmp_path, monkeypatch, synthesis_policy, registry=None):
    """(the Standard written, what `generate` printed), with `registry` as the
    project's `config/topics.yaml` when given."""
    monkeypatch.chdir(tmp_path)
    if registry is not None:
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "topics.yaml").write_text(registry, encoding="utf-8")
    front = (
        f"---\ntopic: Access Review\nowner: IAM\n{synthesis_policy}---\n\n- Review. [NIST AC-2]\n"
    )
    synthesis = tmp_path / "access-review.md"
    synthesis.write_text(front, encoding="utf-8")
    out = tmp_path / "docs" / "standards" / "access-review.md"
    config = {"org": {"name": "Acme", "industry": "H"}, "zardoz": {"host": "https://w.example"}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda _cfg: _Fake(
            "# Access Review Standard\n\n## Purpose\n\nReview access. [NIST AC-2]\n"
        ),
    )
    result = CliRunner().invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis), "--overwrite-edits"],
    )
    assert result.exit_code == 0, result.output
    return out.read_text(encoding="utf-8"), result.output


def test_generate_writes_the_parent_policy_into_a_standard_twice(tmp_path, monkeypatch):
    text = _generate_standard(tmp_path, monkeypatch, "policy: Access Control\n")

    metadata = yaml.safe_load(text.split("---")[1])
    assert metadata["policy"] == "Access Control"
    assert metadata["tier"] == "standard"
    assert "This Standard implements the Access Control Policy." in text
    # After the title, before the first section.
    assert text.index("# Access Review Standard") < text.index("implements the Access Control")
    assert text.index("implements the Access Control") < text.index("## Purpose")


def test_a_standard_with_no_parent_policy_says_so_and_carries_no_stale_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "docs" / "standards" / "access-review.md"
    out.parent.mkdir(parents=True)
    out.write_text(
        "---\ntopic: Access Review\ntier: standard\npolicy: Retired\n---\n\n# Old\n",
        encoding="utf-8",
    )

    text = _generate_standard(tmp_path, monkeypatch, "")

    assert "policy" not in yaml.safe_load(text.split("---")[1])
    assert "This Standard has no parent Policy." in text


def test_a_regenerated_standard_takes_the_syntheses_policy_when_no_registry_names_it(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "docs" / "standards" / "access-review.md"
    out.parent.mkdir(parents=True)
    out.write_text(
        "---\ntopic: Access Review\ntier: standard\npolicy: Old Name\n---\n\n# Old\n",
        encoding="utf-8",
    )

    text = _generate_standard(tmp_path, monkeypatch, "policy: New Name\n")

    assert yaml.safe_load(text.split("---")[1])["policy"] == "New Name"
    assert "Old Name" not in text


def test_synthesize_writes_the_topics_policy_into_the_synthesis(tmp_path, monkeypatch):
    """The hand-off: `generate` is a separate command, so the parent Policy
    has to be in the file `synthesize` writes."""
    monkeypatch.chdir(tmp_path)
    topics = tmp_path / "topics.yaml"
    topics.write_text(TWO_POLICIES, encoding="utf-8")
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    catalog = ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: _Fake("- merged [NIST IA-5]"))

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic-name",
            "Authentication",
            "--topics",
            str(topics),
            "--controls",
            str(catalog),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )

    assert result.exit_code == 0, result.output
    written = next((tmp_path / "synthesis").glob("*.md")).read_text(encoding="utf-8")
    metadata, _ = read_synthesis(written)
    assert metadata["policy"] == "Access Control"
    assert metadata["owner"] == "IAM"


# ---- a Policy's page is declared on the Policy (5919; 1d on PR 439) --------------

PAGE_ON_TOPIC = """
policies:
  - name: Access Control
    owner: CISO
topics:
  - name: Identity Lifecycle
    owner: IAM
    policy: Access Control
    nist_controls: [AC-2]
    confluence: {space: SEC, pages: {policy: Access Control Policy, standard: Std}}
"""


def test_a_topic_declaring_a_policy_page_is_told_to_move_it_to_the_policy(tmp_path):
    report = check_tree(_tree(tmp_path), registry=_registry(PAGE_ON_TOPIC))

    assert report.ok
    assert [m for m in _messages(report, "warning") if "Policy page" in m] == [
        "topic 'Identity Lifecycle' declares a Policy page (SEC/'Access Control Policy') and "
        "names Policy 'Access Control'; declare the page on the Policy, under `policies:`, so "
        "topics sharing it don't each claim it"
    ]


def test_two_topics_and_a_policy_sending_one_page_is_an_error(tmp_path):
    registry = _registry(
        """
policies:
  - name: Access Control
    owner: CISO
    confluence: {space: SEC, pages: {policy: Access Control Policy}}
topics:
  - name: One
    owner: O
    policy: Access Control
    nist_controls: [AC-2]
    confluence: {space: SEC, pages: {policy: Access Control Policy}}
  - name: Two
    owner: O
    policy: Access Control
    nist_controls: [AC-3]
    confluence: {space: SEC, pages: {policy: Access Control Policy}}
"""
    )

    report = check_tree(_tree(tmp_path), registry=registry)

    assert _messages(report, "error") == [
        "topic 'One' publishes its Policy to SEC/'Access Control Policy', which Policy "
        "'Access Control' already claims — one of them would silently overwrite the other",
        "topic 'Two' publishes its Policy to SEC/'Access Control Policy', which Policy "
        "'Access Control' already claims — one of them would silently overwrite the other",
    ]


def test_a_topics_standard_page_is_not_a_policy_page(tmp_path):
    registry = _registry(
        """
topics:
  - name: One
    owner: O
    nist_controls: [AC-2]
    confluence: {space: SEC, pages: {standard: Std, procedure: Proc}}
  - name: Two
    owner: O
    nist_controls: [AC-3]
    confluence: {space: SEC, pages: {standard: Std Two}}
"""
    )

    report = check_tree(_tree(tmp_path), registry=registry)

    assert [m for m in _messages(report) if "Policy page" in m or "claims" in m] == []


def test_the_starter_declares_each_policy_page_on_the_policy_never_on_a_topic():
    registry = load_registry(STARTER)

    on_topics = [t.name for t in registry.topics if dict(t.pages("confluence")).get("policy")]
    on_policies = {p.name: p.page("confluence") for p in registry.policies if p.page("confluence")}

    assert on_topics == []
    assert on_policies == {"Access Control": ("SEC", "Access Control Policy")}


# ---- the registry's Policy, not only the synthesis's (ba on PR 439) -------------

ACCESS_REGISTRY = """
policies:
  - {name: Access Control, owner: CISO}
topics:
  - {name: Access Review, owner: IAM, policy: Access Control, nist_controls: [AC-2]}
"""


def _frontmatter(text):
    return yaml.safe_load(text.split("---")[1])


def test_a_synthesis_written_before_the_key_takes_the_registrys_policy(tmp_path, monkeypatch):
    """Every pre-2a synthesis, and `synthesize --topic` ones, have no `policy`
    key. They must not make `generate` write a false "no parent Policy"."""
    text, output = _generate_standard_with(tmp_path, monkeypatch, "", registry=ACCESS_REGISTRY)

    assert _frontmatter(text)["policy"] == "Access Control"
    assert "This Standard implements the Access Control Policy." in text
    assert "no parent Policy" not in text
    assert "the synthesis names none; it names Policy 'Access Control'" in output


def test_the_registrys_current_policy_wins_over_a_stale_synthesis(tmp_path, monkeypatch):
    text, output = _generate_standard_with(
        tmp_path, monkeypatch, "policy: Old Name\n", registry=ACCESS_REGISTRY
    )

    assert _frontmatter(text)["policy"] == "Access Control"
    assert "Old Name" not in text
    assert "the synthesis names Policy 'Old Name'; it names Policy 'Access Control'" in output


def test_a_registry_that_agrees_says_nothing(tmp_path, monkeypatch):
    _, output = _generate_standard_with(
        tmp_path, monkeypatch, "policy: Access Control\n", registry=ACCESS_REGISTRY
    )

    assert "The parent Policy is" not in output


def test_a_registry_that_does_not_list_the_topic_leaves_the_syntheses_policy(tmp_path, monkeypatch):
    other = ACCESS_REGISTRY.replace("Access Review", "Something Else")

    text, _ = _generate_standard_with(tmp_path, monkeypatch, "policy: Kept\n", registry=other)

    assert _frontmatter(text)["policy"] == "Kept"


def test_a_registry_that_cannot_be_read_leaves_the_syntheses_policy(tmp_path, monkeypatch):
    text, _ = _generate_standard_with(
        tmp_path, monkeypatch, "policy: Kept\n", registry="topics: [{name: T}]\n"
    )

    assert _frontmatter(text)["policy"] == "Kept"


def test_a_registry_naming_no_policy_for_the_topic_says_so_in_the_standard(tmp_path, monkeypatch):
    bare = "topics:\n  - {name: Access Review, owner: IAM, nist_controls: [AC-2]}\n"

    text, output = _generate_standard_with(tmp_path, monkeypatch, "policy: Was\n", registry=bare)

    assert "policy" not in _frontmatter(text)
    assert "This Standard has no parent Policy." in text
    assert "the synthesis names Policy 'Was'; it names no Policy" in output


# ---- check: a Standard that has drifted from its topic's Policy ----------------


def _standard(tmp_path, *, topic, policy):
    root = tmp_path / "docs"
    (root / "standards").mkdir(parents=True)
    front = f"policy: {policy}\n" if policy else ""
    (root / "standards" / "s.md").write_text(
        f"---\ntitle: S\ntier: standard\ntopic: {topic}\nowner: IAM\n{front}---\n\n# S\n\nText.\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize(
    ("policy", "says"),
    [
        (
            "",
            "its `policy:` names no Policy, but topic 'Identity Lifecycle' names 'Access Control'",
        ),
        ("Continuity", "its `policy:` names Policy 'Continuity', but topic 'Identity Lifecycle'"),
    ],
)
def test_a_standard_that_has_drifted_from_its_topics_policy_is_a_warning(tmp_path, policy, says):
    registry = _registry(TWO_POLICIES)
    root = _standard(tmp_path, topic="Identity Lifecycle", policy=policy)

    report = check_tree(root, registry=registry)

    drift = [m for m in _messages(report, "warning") if "its `policy:`" in m]
    assert len(drift) == 1 and drift[0].startswith(says)
    assert drift[0].endswith("regenerate it")


def test_a_standard_that_matches_its_topic_or_has_none_in_the_registry_is_quiet(tmp_path):
    registry = _registry(TWO_POLICIES)

    matching = check_tree(
        _standard(tmp_path, topic="identity lifecycle", policy="ACCESS CONTROL"), registry=registry
    )
    unknown_topic = check_tree(
        _standard(tmp_path / "x", topic="Not In The Registry", policy=""), registry=registry
    )

    assert [m for m in _messages(matching) if "its `policy:`" in m] == []
    assert [m for m in _messages(unknown_topic) if "its `policy:`" in m] == []


def test_a_blank_policy_name_or_owner_is_missing():
    for block in ("{name: '  ', owner: X}", "{name: P, owner: '   '}"):
        with pytest.raises(TopicRegistryError, match="missing `"):
            _registry(f"policies: [{block}]\ntopics: [{{name: T, owner: O}}]")

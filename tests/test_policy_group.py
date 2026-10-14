"""`generate --tier policy --policy NAME`: one Policy over its Standards
(22, part 2b; 80's spec in comment 5919).

The registry and the Standards are hand-written here: which Standards a
Policy governs is read off the files below, never from the code that groups
them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.llm.base import LLMResponse

REGISTRY = """
policies:
  - name: Access Control
    owner: CISO
    cadence: annual
    confluence: {space: SEC, pages: {policy: Access Control Policy}}
  - name: Continuity
    owner: COO
topics:
  - {name: Identity Lifecycle, owner: IAM, policy: Access Control, nist_controls: [AC-2]}
  - {name: Authentication, owner: Security, policy: Access Control, nist_controls: [IA-5]}
  - {name: Backup, owner: Infrastructure, policy: Continuity, nist_controls: [CP-9]}
  - {name: Unfiled, owner: Nobody, nist_controls: [AC-1]}
"""

# The draft the model returns: a section per Standard, one citation tag, and
# a closing list of its own (code writes that one).
GOOD_DRAFT = """# Access Control Policy

## Purpose

Acme controls who can reach its systems.

## Scope

All staff and contractors.

## Identity Lifecycle Standard

Accounts are created, changed and removed on a schedule. [NIST AC-2]

## Authentication Standard

People prove who they are before they are let in.

## Standards under this Policy

- The model's own list, which must not survive.
"""


class _Fake:
    def __init__(self, text):
        self.text = text
        self.prompts: list[str] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        self.prompts.append(prompt)
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def _standard(root, stem, *, topic, title, owner, body="Requirements.", extra=""):
    directory = root / "docs" / "standards"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.md").write_text(
        f"---\ntitle: {title}\ntier: standard\ntopic: {topic}\nowner: {owner}\n{extra}---\n\n"
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def _project(tmp_path, monkeypatch, *, draft=GOOD_DRAFT, config=None, standards=True):
    """A project with the registry above and, by default, a Standard for each
    topic that names a Policy. Returns the fake provider."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "topics.yaml").write_text(REGISTRY, encoding="utf-8")
    if standards:
        _standard(
            tmp_path,
            "identity-lifecycle",
            topic="Identity Lifecycle",
            title="Identity Lifecycle Standard",
            owner="IAM",
            body="Accounts are reviewed every quarter. [NIST AC-2]",
        )
        _standard(
            tmp_path,
            "authentication",
            topic="Authentication",
            title="Authentication Standard",
            owner="Security",
        )
        _standard(
            tmp_path, "backup", topic="Backup", title="Backup Standard", owner="Infrastructure"
        )
    fake = _Fake(draft)
    cfg = config or {"org": {"name": "Acme", "industry": "Health"}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli_mod, "get_provider", lambda _cfg: fake)
    return fake


def _run(*args):
    return CliRunner().invoke(cli_mod.cli, ["generate", "--tier", "policy", *args])


def _front(text):
    return yaml.safe_load(text.split("---")[1])


def _policy_text(tmp_path, stem="access-control"):
    return (tmp_path / "docs" / "policies" / f"{stem}.md").read_text(encoding="utf-8")


# ---- which Standards, and what is written ------------------------------------------


def test_a_policy_is_drafted_from_exactly_the_standards_that_name_it(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    text = _policy_text(tmp_path)
    # Two Standards name Access Control; the third names Continuity.
    assert "- [Identity Lifecycle Standard](../standards/identity-lifecycle.md) — IAM" in text
    assert "- [Authentication Standard](../standards/authentication.md) — Security" in text
    assert "Backup Standard" not in text
    prompt = fake.prompts[0]
    assert "Accounts are reviewed every quarter." in prompt
    assert "Backup Standard" not in prompt


def test_the_closing_section_is_the_codes_and_the_models_own_is_dropped(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)

    _run("--policy", "Access Control")

    text = _policy_text(tmp_path)
    assert text.count("## Standards under this Policy") == 1
    assert "The model's own list" not in text
    # The closing section is last.
    assert text.rstrip().splitlines()[-1].startswith("- [Authentication Standard]")


def test_a_policy_carries_no_control_tags_and_the_run_says_so(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch)

    result = _run("--policy", "Access Control")

    assert "[NIST AC-2]" not in _policy_text(tmp_path)
    assert "Removed 1 citation tag(s) from the Policy" in result.output
    # The Standard's own tag is not shown to the model either.
    assert "[NIST AC-2]" not in fake.prompts[0]


def test_the_model_is_not_shown_source_blocks(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch)
    _standard(
        tmp_path,
        "identity-lifecycle",
        topic="Identity Lifecycle",
        title="Identity Lifecycle Standard",
        owner="IAM",
        body=(
            "Accounts are reviewed.\n\n"
            '<details data-policyforge="source">\n'
            "<summary>AC-2</summary>\n\n"
            "QUOTED CATALOG WORDING THAT A POLICY NEVER RESTATES\n\n"
            "</details>"
        ),
    )

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    assert "Accounts are reviewed." in fake.prompts[0]
    assert "QUOTED CATALOG WORDING" not in fake.prompts[0]


def test_a_standard_the_draft_gave_no_section_is_given_one_and_named(tmp_path, monkeypatch):
    draft = GOOD_DRAFT.replace("## Authentication Standard", "## Something Else")
    _project(tmp_path, monkeypatch, draft=draft)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    text = _policy_text(tmp_path)
    assert "## Authentication Standard\n\nThis Policy governs the Authentication Standard" in text
    assert "1 Standard(s) had no section in the draft" in result.output
    assert "Authentication Standard" in result.output.split("plain one naming it:")[1]


def test_a_draft_without_a_title_is_given_the_policys_name(tmp_path, monkeypatch):
    draft = GOOD_DRAFT.replace("# Access Control Policy\n\n", "", 1)
    _project(tmp_path, monkeypatch, draft=draft)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    assert _policy_text(tmp_path).split("---")[2].lstrip().startswith("# Access Control")


# ---- binding and frontmatter -------------------------------------------------------


def test_the_policy_is_bound_to_the_page_its_registry_entry_declares(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)

    _run("--policy", "Access Control")

    front = _front(_policy_text(tmp_path))
    assert front["tier"] == "policy"
    assert front["policy"] == "Access Control"
    assert front["owner"] == "CISO"
    assert front["confluence"] == {"space": "SEC", "title": "Access Control Policy"}
    assert "topic" not in front


def test_a_policy_with_no_declared_page_gets_no_binding(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _standard(tmp_path, "backup", topic="Backup", title="Backup Standard", owner="Infra")
    draft = (
        "# Continuity Policy\n\n## Purpose\n\nKeep running.\n\n## Backup Standard\n\nWe back up.\n"
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda _cfg: _Fake(draft))

    result = _run("--policy", "Continuity")

    assert result.exit_code == 0, result.output
    front = _front(_policy_text(tmp_path, "continuity"))
    assert "confluence" not in front
    assert front["owner"] == "COO"


def test_the_run_is_recorded_with_the_policy_and_its_standards(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)

    result = _run("--policy", "Access Control", "--history-dir", str(tmp_path / "h"))

    assert result.exit_code == 0, result.output
    versions = list((tmp_path / "h").rglob("index.jsonl"))
    assert versions, "no version was recorded"
    recorded = "".join(path.read_text(encoding="utf-8") for path in versions)
    assert "identity-lifecycle.md" in recorded and "authentication.md" in recorded


# ---- what is refused before a model is built --------------------------------------


def _no_provider(monkeypatch):
    def refuse(_cfg):
        raise AssertionError("a provider was built for a run that should have been refused")

    monkeypatch.setattr(cli_mod, "get_provider", refuse)


def test_a_missing_standard_refuses_the_whole_policy_and_names_the_topic(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    (tmp_path / "docs" / "standards" / "authentication.md").unlink()
    _no_provider(monkeypatch)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 2
    assert "have no Standard in the content tree yet" in result.output
    assert "Authentication" in result.output
    assert not (tmp_path / "docs" / "policies").exists()


def test_two_standards_for_one_topic_refuse_and_name_both_files(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _standard(
        tmp_path,
        "authentication-old",
        topic="authentication",
        title="Authentication Standard (old)",
        owner="Security",
    )
    _no_provider(monkeypatch)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 2
    assert "has more than one Standard" in result.output
    assert "standards/authentication.md" in result.output
    assert "standards/authentication-old.md" in result.output


def test_a_policy_the_registry_does_not_declare_is_refused_with_the_declared_ones(
    tmp_path, monkeypatch
):
    _project(tmp_path, monkeypatch)
    _no_provider(monkeypatch)

    result = _run("--policy", "Physical Security")

    assert result.exit_code == 2
    assert "declares no Policy named 'Physical Security'" in result.output
    assert "'Access Control'" in result.output and "'Continuity'" in result.output


def test_a_declared_policy_no_topic_names_is_refused(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    registry = REGISTRY.replace("policy: Continuity", "policy: Access Control")
    (tmp_path / "config" / "topics.yaml").write_text(registry, encoding="utf-8")
    _no_provider(monkeypatch)

    result = _run("--policy", "Continuity")

    assert result.exit_code == 2
    assert "names Policy 'Continuity', so it has no Standards" in result.output


def test_the_policy_option_needs_the_policy_tier_and_takes_no_synthesis(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _no_provider(monkeypatch)
    synthesis = tmp_path / "s.md"
    synthesis.write_text("---\ntopic: T\n---\n\n- x\n", encoding="utf-8")

    wrong_tier = CliRunner().invoke(
        cli_mod.cli, ["generate", "--tier", "standard", "--policy", "Access Control"]
    )
    with_synthesis = _run("--policy", "Access Control", "--synthesis", str(synthesis))
    neither = _run()

    assert wrong_tier.exit_code == 2 and "needs --tier policy" in wrong_tier.output
    assert with_synthesis.exit_code == 2 and "neither --synthesis nor --standard" in (
        with_synthesis.output
    )
    assert neither.exit_code == 2 and "--synthesis is required" in neither.output


def test_a_missing_registry_is_named_not_assumed(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _no_provider(monkeypatch)

    result = _run("--policy", "Access Control", "--topics", str(tmp_path / "nope.yaml"))

    assert result.exit_code == 2
    assert "nope.yaml does not exist" in result.output


# ---- the licensing boundary --------------------------------------------------------

HOSTED = {
    "org": {"name": "Acme", "industry": "Health"},
    "llm": {"provider": "openrouter", "model": "vendor/model"},
}


def test_one_licensed_standard_makes_the_policy_licensed_and_a_hosted_model_is_refused(
    tmp_path, monkeypatch
):
    _project(tmp_path, monkeypatch, config=HOSTED)
    _standard(
        tmp_path,
        "authentication",
        topic="Authentication",
        title="Authentication Standard",
        owner="Security",
        extra="generated_by:\n  content_class: licensed\n",
    )
    _no_provider(monkeypatch)

    result = _run("--policy", "Access Control")

    assert result.exit_code != 0
    assert "Refusing to draft from the Standards under Policy 'Access Control'" in result.output
    assert "licensed" in result.output
    assert not (tmp_path / "docs" / "policies").exists()


def test_the_policy_records_its_standards_catalogs_and_names_one_that_recorded_none(
    tmp_path, monkeypatch
):
    """443 part 2: a Policy drafted from Standards carries their catalogs,
    each once, and names a Standard that recorded none."""
    _project(tmp_path, monkeypatch)
    entry = "{catalog: invented-csf, sha256: '" + "a" * 64 + "', library: {version: '11.7'}}"
    for stem, topic, owner in (
        ("authentication", "Authentication", "Security"),
        ("identity-lifecycle", "Identity Lifecycle", "IAM"),
    ):
        _standard(
            tmp_path,
            stem,
            topic=topic,
            title=f"{topic} Standard",
            owner=owner,
            extra=f"generated_by:\n  catalogs: [{entry}]\n" if stem == "authentication" else "",
        )

    assert _run("--policy", "Access Control").exit_code == 0
    stamp = _front(_policy_text(tmp_path))["generated_by"]
    assert stamp["catalogs"] == [
        {"catalog": "invented-csf", "sha256": "a" * 64, "library": {"version": "11.7"}}
    ]
    assert [Path(p).name for p in stamp["catalogs_not_recorded"]] == ["identity-lifecycle.md"]


def test_the_same_group_with_no_licensed_standard_goes_to_the_hosted_model(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch, config=HOSTED)

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    assert fake.prompts


def test_a_licensed_standard_in_another_policy_does_not_taint_this_one(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch, config=HOSTED)
    _standard(
        tmp_path,
        "backup",
        topic="Backup",
        title="Backup Standard",
        owner="Infrastructure",
        extra="generated_by:\n  content_class: licensed\n",
    )

    result = _run("--policy", "Access Control")

    assert result.exit_code == 0, result.output
    assert fake.prompts


# ---- a hand-edited Policy is not replaced ------------------------------------------


def test_a_policy_a_person_edited_is_not_replaced_without_the_flag(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    assert _run("--policy", "Access Control").exit_code == 0
    path = tmp_path / "docs" / "policies" / "access-control.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nA person's addition.\n", encoding="utf-8")

    refused = _run("--policy", "Access Control")
    kept = path.read_text(encoding="utf-8")
    forced = _run("--policy", "Access Control", "--overwrite-edits")

    assert refused.exit_code != 0
    assert "A person's addition." in kept
    assert forced.exit_code == 0, forced.output
    assert "A person's addition." not in path.read_text(encoding="utf-8")


def test_a_single_topic_policy_file_is_not_carried_onto_the_groups(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    out = tmp_path / "docs" / "policies" / "access-control.md"
    out.parent.mkdir(parents=True)
    out.write_text(
        "---\ntitle: Old\ntier: policy\ntopic: Authentication\nowner: Someone Else\n"
        "confluence: {space: OLD, title: Old Page}\n---\n\n# Old\n",
        encoding="utf-8",
    )

    result = _run("--policy", "Access Control", "--overwrite-edits")

    assert result.exit_code == 0, result.output
    front = _front(_policy_text(tmp_path))
    assert "topic" not in front
    assert front["owner"] == "CISO"
    assert front["confluence"] == {"space": "SEC", "title": "Access Control Policy"}
    assert "not carried onto the group's Policy" in result.output


# ---- edit-topic and pull reach the Policy page ----------------------------------

from policyforge.edit import tree as edit_tree  # noqa: E402
from policyforge.export.pull import pages_from_registry  # noqa: E402
from policyforge.topics.registry import parse_registry  # noqa: E402


def _registry_objects():
    registry = parse_registry(yaml.safe_load(REGISTRY))
    topic = next(t for t in registry.topics if t.name == "Authentication")
    return registry, topic, registry.policy("Access Control")


def _document(directory, stem, front, title="Doc"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.md").write_text(
        f"---\ntitle: {title}\n{front}---\n\n# {title}\n\nText.\n", encoding="utf-8"
    )


def _tree(tmp_path):
    docs = tmp_path / "docs"
    _document(
        docs / "standards", "authentication", "tier: standard\ntopic: Authentication\n", "Authn"
    )
    return docs


def test_a_topic_with_no_policy_document_of_its_own_is_offered_its_policys(tmp_path):
    docs = _tree(tmp_path)
    _document(
        docs / "policies",
        "access-control",
        "tier: policy\npolicy: Access Control\n",
        "Access Control Policy",
    )
    _, topic, policy = _registry_objects()

    selection = edit_tree.select_topic_files(docs, topic, policy=policy)

    assert list(selection.files) == ["policy", "standard"]
    assert selection.files["policy"].relative == "policies/access-control.md"
    assert selection.files["policy"].matched_by == "names Policy 'Access Control'"


def test_a_policy_file_bound_to_the_declared_page_is_found_without_a_policy_key(tmp_path):
    docs = _tree(tmp_path)
    _document(
        docs / "policies",
        "pulled",
        "tier: policy\nconfluence: {space: SEC, title: Access Control Policy}\n",
    )
    _, topic, policy = _registry_objects()

    selection = edit_tree.select_topic_files(docs, topic, policy=policy)

    assert selection.files["policy"].matched_by == "bound to Policy page 'Access Control Policy'"


def test_the_topics_own_policy_document_wins_and_the_groups_is_not_added(tmp_path):
    docs = _tree(tmp_path)
    _document(docs / "policies", "mine", "tier: policy\ntopic: Authentication\n")
    _document(docs / "policies", "group", "tier: policy\npolicy: Access Control\n")
    _, topic, policy = _registry_objects()

    selection = edit_tree.select_topic_files(docs, topic, policy=policy)

    assert selection.files["policy"].relative == "policies/mine.md"
    assert not selection.ambiguous


def test_another_topics_policy_document_is_not_the_groups(tmp_path):
    docs = _tree(tmp_path)
    _document(docs / "policies", "other", "tier: policy\ntopic: Backup\npolicy: Access Control\n")
    _, topic, policy = _registry_objects()

    selection = edit_tree.select_topic_files(docs, topic, policy=policy)

    assert list(selection.files) == ["standard"]


def test_two_files_claiming_the_groups_policy_are_named_not_chosen_between(tmp_path):
    docs = _tree(tmp_path)
    _document(docs / "policies", "a", "tier: policy\npolicy: Access Control\n")
    _document(docs / "policies", "b", "tier: policy\npolicy: access control\n")
    _, topic, policy = _registry_objects()

    selection = edit_tree.select_topic_files(docs, topic, policy=policy)

    assert selection.ambiguous == {"policy": ["policies/a.md", "policies/b.md"]}


def test_no_policy_is_offered_without_one_or_when_the_tiers_exclude_it(tmp_path):
    docs = _tree(tmp_path)
    _document(docs / "policies", "group", "tier: policy\npolicy: Access Control\n")
    _, topic, policy = _registry_objects()

    unaware = edit_tree.select_topic_files(docs, topic)
    narrowed = edit_tree.select_topic_files(docs, topic, tiers={"standard"}, policy=policy)

    assert list(unaware.files) == ["standard"]
    assert list(narrowed.files) == ["standard"]


def test_edit_topic_reads_the_parent_policy_file_by_the_registry(tmp_path, monkeypatch):
    docs = _tree(tmp_path)
    _document(docs / "policies", "access-control", "tier: policy\npolicy: Access Control\n")
    registry = tmp_path / "topics.yaml"
    registry.write_text(REGISTRY, encoding="utf-8")

    class _Stop(Exception):
        pass

    def stop(_config):
        raise _Stop

    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"model": "m"}})
    monkeypatch.setattr(cli_mod, "get_provider", stop)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-topic",
            "--instruction",
            "Accounts are reviewed monthly.",
            "--topic-name",
            "Authentication",
            "--topics",
            str(registry),
            "--content-dir",
            str(docs),
            "--out-dir",
            str(tmp_path / "edits"),
        ],
    )

    assert "Read policies/access-control.md (policy; matched by names Policy 'Access Control')" in (
        result.output
    )
    assert "2 file(s)" in result.output


def test_pull_covers_each_policys_own_page_once_and_skips_one_with_no_page(tmp_path):
    registry = parse_registry(
        yaml.safe_load(
            """
policies:
  - {name: Access Control, owner: CISO, confluence: {space: SEC, pages: {policy: AC Policy}}}
  - {name: Continuity, owner: COO}
topics:
  - name: Authentication
    owner: Security
    policy: Access Control
    confluence: {space: ENG, pages: {standard: Authn Standard, procedure: Authn Procedure}}
  - name: Backup
    owner: Infra
    policy: Continuity
    confluence: {space: ENG, pages: {standard: Backup Standard}}
"""
        )
    )

    targets = pages_from_registry(registry)

    assert targets == [
        ("ENG", "Authn Standard", "standard"),
        ("ENG", "Authn Procedure", "procedure"),
        ("ENG", "Backup Standard", "standard"),
        ("SEC", "AC Policy", "policy"),
    ]


def test_a_page_a_topic_and_its_policy_both_declare_is_pulled_once():
    registry = parse_registry(
        yaml.safe_load(
            """
policies:
  - {name: Access Control, owner: CISO, confluence: {space: SEC, pages: {policy: AC Policy}}}
topics:
  - name: Authentication
    owner: Security
    policy: Access Control
    confluence: {space: SEC, pages: {policy: AC Policy, standard: Authn Standard}}
"""
        )
    )

    targets = pages_from_registry(registry)

    assert sorted(targets) == [
        ("SEC", "AC Policy", "policy"),
        ("SEC", "Authn Standard", "standard"),
    ]

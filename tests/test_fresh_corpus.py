"""The fresh-corpus comparison (#262): what it generates, and how it tells a
finding or placeholder the committed corpus never showed from one it did.

Nothing here calls a model or fetches: the comparison is pure, and the
generation is checked by the commands it would run.
"""

from __future__ import annotations

from pathlib import Path

from scripts import fresh_corpus as fc

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# What a run generates
# --------------------------------------------------------------------------


def test_the_runs_registry_is_what_init_writes_for_the_example_profile():
    """The run no longer copies the pool whole (33 topics, three of them
    unanchored until a role gives them anchors): it builds the registry from
    PROFILE, by the tip's own rule, in a subprocess like every other step."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
           "PYTHONIOENCODING": "utf-8"}  # fmt: skip
    done = subprocess.run([sys.executable, "-c", fc.registry_program()], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8")  # fmt: skip

    assert done.returncode == 0, done.stderr
    names = fc.topic_names(done.stdout)
    assert len(names) == 25
    assert {"HIPAA Privacy Practices", "Breach Notification", "Privacy"} <= set(names)
    assert "Identity Lifecycle & Access Review" in names
    assert "Third-Party AI & Data Supply Chain" in names
    assert not {"Data for AI Systems", "Transparency to Affected People"} & set(names)


def test_the_slug_is_the_one_synthesize_writes():
    """`synthesize` names its file by this rule (cli/documents.py); written
    out by hand here so the two can't agree by sharing code."""
    assert fc.slug("Identity Lifecycle & Access Review") == "identity-lifecycle-access-review"
    assert fc.slug("Logging, Monitoring & Alerting") == "logging-monitoring-alerting"
    assert fc.slug("Third-Party AI & Data Supply Chain") == "third-party-ai-data-supply-chain"


def test_each_topic_is_synthesized_then_a_standard_then_a_procedure_naming_it(tmp_path):
    steps = fc.steps("Incident Response", tmp_path, ["pf"])

    assert [name for name, _ in steps] == ["synthesize", "standard", "procedure"]
    synthesize, standard, procedure = (command for _, command in steps)
    assert synthesize[:4] == ["pf", "synthesize", "--topic-name", "Incident Response"]
    assert standard[standard.index("--tier") + 1] == "standard"
    std_out = standard[standard.index("--out") + 1]
    assert procedure[procedure.index("--tier") + 1] == "procedure"
    assert procedure[procedure.index("--standard") + 1] == std_out, "the Procedure names it"


def test_only_the_public_domain_catalogs_the_committed_corpus_used_are_loaded(tmp_path):
    """The committed corpus's README names them, and the two PHI topics add
    the Privacy and Breach Notification Rules (#420). The AI topics anchor on
    the AI RMF, so leaving it out drops five topics silently (the 1.6.1
    matrix); leaving these out drops the PHI topics the same way."""
    (_, synthesize), *_ = fc.steps("AI Governance & Accountability", tmp_path, ["pf"])
    loaded = {
        synthesize[i + 1].split("/")[2] for i, a in enumerate(synthesize) if a == "--controls"
    }

    assert loaded == {
        "nist-800-53-r5",
        "hipaa-security-rule",
        "hipaa-privacy-rule",
        "hipaa-breach-notification-rule",
        "fedramp",
        "arc-ampe",
        "nist-ai-rmf",
        "nist-ai-rmf-playbook",
    }
    for name in loaded:
        assert (ROOT / "data" / "frameworks" / name / "controls.json").exists()


def test_the_run_config_is_the_local_pinned_one_with_the_example_org(tmp_path):
    import yaml

    config = yaml.safe_load(fc._org_config("qwen3:14b-pf32", tmp_path / "l.jsonl"))

    assert config["llm"]["provider"] == "local"
    assert config["llm"]["model"] == "qwen3:14b-pf32"
    assert config["org"] == {"name": "Example Org"}


# --------------------------------------------------------------------------
# Finding shapes
# --------------------------------------------------------------------------


def test_two_findings_of_one_kind_on_two_documents_have_one_shape():
    a = 'line 36: states a cited requirement as a permission — "Staff may rotate keys"'
    b = 'line 112: states a cited requirement as a permission — "Admins may review logs"'

    assert fc.shape(a) == fc.shape(b) == "line N: states a cited requirement as a permission — '…'"


def test_different_kinds_keep_different_shapes():
    assert fc.shape("line 3: binds but cites nothing — 'x'") != fc.shape(
        "line 3: states a cited requirement as a recommendation — 'x'"
    )


def test_bracketed_labels_are_taken_out_of_a_shape():
    assert fc.shape("cites [NIST 800-53 AC-2] twice") == fc.shape("cites [HIPAA 164.308] twice")


def test_a_shape_only_the_fresh_corpus_has_comes_first_with_an_example():
    """First although it sorts last by name and is on fewer documents than a
    shared shape: being new is what puts it first."""
    old = {"s/a.md": ["line 1: binds but cites nothing — 'x'"]}
    new = {
        "s/b.md": ["line 9: binds but cites nothing — 'y'"],
        "s/c.md": ["line 2: binds but cites nothing — 'w'", "line 4: zz a new kind — 'z'"],
    }

    rows = fc.compare_shapes(old, new)

    assert rows[0]["shape"] == "line N: zz a new kind — '…'"
    assert (rows[0]["old"], rows[0]["new"]) == (0, 1)
    assert rows[0]["example"] == ("s/c.md", "line 4: zz a new kind — 'z'")
    assert (rows[1]["old"], rows[1]["new"]) == (1, 2)


def test_a_shape_is_counted_once_per_document():
    rows = fc.compare_shapes({}, {"s/a.md": ["line 1: x — 'a'", "line 2: x — 'b'"]})

    assert [(r["old"], r["new"]) for r in rows] == [(0, 1)]


def test_a_shape_only_the_committed_corpus_had_is_still_listed():
    rows = fc.compare_shapes({"s/a.md": ["gone — 'x'"]}, {})

    assert [(r["shape"], r["old"], r["new"]) for r in rows] == [("gone — '…'", 1, 0)]


# --------------------------------------------------------------------------
# Placeholder forms
# --------------------------------------------------------------------------


def test_label_forms_from_labels_the_model_writes():
    """Labels as generated documents carry them, not from the function's rules."""
    assert fc.label_form("Access Review Frequency") == "Aa"
    assert fc.label_form("Retention Period") == "Aa"
    assert fc.label_form("SIEM Platform") == "A Aa"
    assert fc.label_form("CISO") == "A"
    assert fc.label_form("Assignment: organization-defined frequency") == "Aa: a-a"
    assert fc.label_form("Retention Period (90 days)") == "Aa (N a)"
    assert fc.label_form("Selection (one or more): weekly; monthly") == "Aa (a): a; a"


def test_the_reader_partitions_a_documents_placeholders():
    classes = fc.placeholder_classes(
        "The [Identity Provider] enforces MFA every [Access Review Frequency]."
    )

    assert classes["roles left"] == ["Identity Provider"]
    assert classes["unfilled"] == ["Access Review Frequency"]


def test_the_report_names_a_placeholder_form_the_committed_corpus_never_had(monkeypatch):
    monkeypatch.setattr(fc, "_check_findings", lambda docs: {d: [] for d in docs})
    old = {"standards/a.md": "Review every [Access Review Frequency]."}
    new = {
        "standards/b.md": "Review every [Retention Period].",
        "standards/c.md": "Send alerts to the [SIEM Platform].",
    }

    report = fc.comparison_report(old, new)

    line = next(row for row in report.splitlines() if row.startswith("- **unfilled:**"))
    assert line.endswith("1 forms only in the fresh one")
    assert "`A Aa`: 1, e.g. `[SIEM Platform]` in standards/c.md" in report
    assert "`[Retention Period]`" not in report, "same form as one the committed corpus had"


def test_comparing_the_committed_corpus_with_itself_finds_nothing_new():
    """The whole comparison, over the real 128, against a copy of itself:
    every shape and form is shared, so nothing is reported as new."""
    corpus = ROOT / fc.COMMITTED
    docs = fc._read(corpus, ".md.txt")
    assert len(docs) == 128

    report = fc.comparison_report(docs, dict(docs))

    table = [row for row in report.splitlines() if row.startswith("| ") and "shape" not in row]
    assert table, "the committed corpus has findings"
    assert all(row.split("|")[1].strip() == row.split("|")[2].strip() for row in table)
    assert report.count("0 forms only in the fresh one") == 2, "unfilled, and unread brackets"
    # roles left, unresolved citations, and the bracketed prose the reader
    # leaves as written (267): three classes are compared by label.
    assert report.count("0 labels only in the fresh one") == 3


def test_without_heavy_a_step_runs_as_is():
    assert fc.queued(["pf", "map"], None, Path("bash"), "b5") == ["pf", "map"]


def test_with_heavy_each_step_takes_the_slot_under_the_handle():
    queued = fc.queued(["pf", "map"], Path("heavy"), Path("bash"), "b5")

    assert queued == ["bash", "heavy", "b5", "fresh-corpus", "--", "pf", "map"]


# --------------------------------------------------------------------------
# Brackets the reader didn't read: a second instrument (1d on PR 265)
# --------------------------------------------------------------------------


def test_a_bracket_the_reader_cannot_parse_is_reported_unread():
    """#243's shape: a comma label the reader's class can't take, so through
    the reader alone it is never filled and never reported."""
    assert fc.unread_brackets("Owner: [Zebra, Lion and Tiger Office].") == [
        "Zebra, Lion and Tiger Office"
    ]


def test_links_images_wiki_links_placeholders_and_citation_tags_are_not_unread():
    text = (
        "See [the guide](https://x), ![logo](a.png), [[Wiki Page]], the [Identity Provider], "
        "every [Access Review Frequency], [NIST 800-53 AC-2] and [NIST 800-53 AC-7(3)]."
    )

    assert fc.unread_brackets(text) == []


def test_a_shorthand_citation_with_a_comma_list_is_read():
    """`[NIST AC-7(3), AC-8]` was read by neither reader until #17: `NIST`
    alone named no one catalog and a comma ended the id. It is now the
    citation form the user confirmed (two 800-53 ids under `NIST`), so it is
    read; `[Zebra, Lion and Tiger Office]` above still shows the unread case."""
    assert fc.unread_brackets("Lock the account [NIST AC-7(3), AC-8].") == []


def test_an_unread_bracket_only_the_fresh_corpus_has_comes_out_first(monkeypatch):
    monkeypatch.setattr(fc, "_check_findings", lambda docs: {d: [] for d in docs})
    old = {"standards/a.md": "Set by [Assignment: organization-defined frequency]."}
    new = {
        "standards/b.md": "Set by [Assignment: organization-defined period].",
        "standards/c.md": "Owner: [Zebra, Lion and Tiger Office].",
    }

    report = fc.comparison_report(old, new)

    section = report.split("# Brackets the placeholder reader didn't read")[1]
    assert "1 forms only in the fresh one" in section
    assert "`[Zebra, Lion and Tiger Office]` in standards/c.md" in section
    assert "organization-defined period" not in section, "same form as the committed one"


def test_the_committed_corpus_has_this_many_unread_brackets():
    """The extent, so a scan that silently stops matching (or matches
    everything) moves a pinned number, not merely a nonzero one. Measured at
    9fdc30ed; 1d's independent prototype read 2,249 in 847 labels. Re-pinned
    for #268: the placeholder class now takes a balanced parenthesis, so 488
    spans in 266 labels moved from unread to read (2,262 in 867 before).
    Re-pinned for #17: 81 spans in 67 labels moved from unread to read, every
    one a citation tag that now resolves (short `NIST` tags, comma-listed
    ids, a baseline qualifier list, `NIST AI RMF Map 1, NIST AI RMF Map 1.1`);
    none moved the other way (1,774 in 601 before; ba diffed the two lists)."""
    docs = fc._read(ROOT / fc.COMMITTED, ".md.txt")
    spans = [span for text in docs.values() for span in fc.unread_brackets(text)]

    assert (len(spans), len(set(spans))) == (1693, 534)


# --------------------------------------------------------------------------
# The Policy step (22, part 2b)
# --------------------------------------------------------------------------

TWO_POLICY_REGISTRY = """
policies:
  - {name: Access Control, owner: CISO}
  - {name: Business Continuity, owner: COO}
topics:
  - {name: Identity, owner: IAM, policy: Access Control, nist_controls: [AC-2]}
  - {name: Authentication, owner: IAM, policy: Access Control, nist_controls: [IA-5]}
  - {name: Backup, owner: Infra, policy: Business Continuity, nist_controls: [CP-9]}
  - {name: Loose, owner: Nobody, nist_controls: [AC-1]}
"""


def test_each_policy_whose_topics_were_all_generated_gets_one_command(tmp_path):
    commands, left_out = fc.policy_steps(
        TWO_POLICY_REGISTRY, {"Identity", "Authentication", "Backup", "Loose"}, tmp_path, ["pf"]
    )

    assert left_out == []
    assert [step for step, _ in commands] == ["policy", "policy"]
    first = commands[0][1]
    assert first[:5] == ["pf", "generate", "--tier", "policy", "--policy"]
    assert first[5] == "Access Control"
    assert first[first.index("--out") + 1] == str(tmp_path / "policies" / "access-control.md")
    assert commands[1][1][5] == "Business Continuity"
    assert "business-continuity.md" in commands[1][1][-1]


def test_a_policy_with_a_topic_not_generated_is_left_out_and_named(tmp_path):
    commands, left_out = fc.policy_steps(
        TWO_POLICY_REGISTRY, {"Identity", "Backup"}, tmp_path, ["pf"]
    )

    assert [c[5] for _, c in commands] == ["Business Continuity"]
    assert left_out == ["Access Control"]


def test_the_example_registry_declares_twelve_policies_and_a_full_run_drafts_each():
    registry = (ROOT / "config" / "topics.example.yaml").read_text(encoding="utf-8")
    names = fc.topic_names(registry)

    commands, left_out = fc.policy_steps(registry, set(names), Path("out"), ["pf"])

    assert len(fc.policy_names(registry)) == 12
    assert len(commands) == 12 and left_out == []


def test_the_run_config_points_generate_policy_at_the_runs_own_standards(tmp_path):
    import yaml

    windows = Path("C:/Users/someone/state/corpora/run one's")
    config = yaml.safe_load(fc._org_config("m", tmp_path / "l.jsonl", content_dir=windows))

    assert config["zardoz"]["content_dir"] == str(windows)
    assert "zardoz" not in yaml.safe_load(fc._org_config("m", tmp_path / "l.jsonl"))

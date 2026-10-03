"""edit/ tests — the Confluence editing harness.

This is the only thing in the project that modifies something outside the
repo, so most of what's tested here is refusal: refusing to publish without
`--apply`, refusing a page whose macros wouldn't survive the round trip,
refusing a stale write, and refusing to let a rewrite quietly drop framework
citations.

No test makes a network call; the Confluence fetch/publish functions and the
LLM provider are all faked.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

DOCUMENT = """# Access Control Standard

## Purpose

This standard governs access to systems holding ePHI.

## Requirements

- Accounts must be reviewed quarterly. [NIST AC-2 | HIPAA 164.308(a)(4)(ii)(C)]
- Access must follow least privilege. [NIST AC-6]

## Exceptions

Exceptions require written approval from [Security Officer].
"""


class ScriptedProvider:
    """Returns queued responses in order, recording every prompt."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(text=self.responses.pop(0), model="fake")

    def check(self):
        return True


def _plan_json(steps=None, risks=None, out_of_scope=None):
    return json.dumps(
        {
            "steps": steps
            if steps is not None
            else [
                {
                    "kind": "modify",
                    "target": "Requirements",
                    "summary": "Change review cadence from quarterly to monthly.",
                    "rationale": "The instruction asks for monthly reviews.",
                }
            ],
            "risks": risks or [],
            "out_of_scope": out_of_scope or [],
        }
    )


def _plan(**kwargs):
    from policyforge.edit.plan import build_edit_plan

    provider = ScriptedProvider(_plan_json(**kwargs))
    return build_edit_plan("Make reviews monthly.", DOCUMENT, provider, page_title="ACS")


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def test_plan_parses_steps_risks_and_refusals():
    plan = _plan(risks=["Shortening the cadence increases workload."], out_of_scope=["Nothing."])

    assert len(plan.steps) == 1
    assert plan.steps[0].kind == "modify"
    assert plan.steps[0].target == "Requirements"
    assert plan.risks == ["Shortening the cadence increases workload."]
    assert plan.out_of_scope == ["Nothing."]
    assert not plan.is_empty


def test_plan_survives_a_fenced_json_response():
    """Models wrap JSON in code fences often enough that failing on it would
    be a needless source of flakiness."""
    from policyforge.edit.plan import build_edit_plan

    provider = ScriptedProvider("Here you go:\n```json\n" + _plan_json() + "\n```\n")
    plan = build_edit_plan("Make reviews monthly.", DOCUMENT, provider)

    assert len(plan.steps) == 1


def test_plan_raises_when_the_response_is_not_json():
    from policyforge.edit.plan import build_edit_plan

    provider = ScriptedProvider("I would change the review cadence.")
    with pytest.raises(ValueError, match="did not return a JSON object"):
        build_edit_plan("Make reviews monthly.", DOCUMENT, provider)


def test_step_targeting_a_nonexistent_section_is_demoted_to_a_risk():
    """The executor works by locating the target; a step pointing at nothing
    would either be dropped silently or invite inventing a section."""
    plan = _plan(
        steps=[
            {
                "kind": "modify",
                "target": "Enforcement",
                "summary": "Tighten enforcement.",
                "rationale": "x",
            }
        ]
    )

    assert plan.steps == []
    assert any("isn't in the page" in r for r in plan.risks)


def test_an_add_step_may_target_a_section_that_does_not_exist_yet():
    plan = _plan(
        steps=[
            {
                "kind": "add",
                "target": "Enforcement",
                "summary": "Add an enforcement section.",
                "rationale": "x",
            }
        ]
    )

    assert len(plan.steps) == 1
    assert plan.steps[0].kind == "add"


def test_a_malformed_step_is_discarded_and_reported():
    plan = _plan(steps=[{"kind": "obliterate", "target": "Requirements", "summary": "..."}])

    assert plan.steps == []
    assert any("malformed" in r for r in plan.risks)


def test_planner_refuses_empty_input():
    from policyforge.edit.plan import build_edit_plan

    with pytest.raises(ValueError, match="instruction is empty"):
        build_edit_plan("", DOCUMENT, ScriptedProvider())
    with pytest.raises(ValueError, match="document is empty"):
        build_edit_plan("Do a thing.", "", ScriptedProvider())


# --------------------------------------------------------------------------
# Applying, and checking what came back
# --------------------------------------------------------------------------


def test_apply_sends_the_approved_plan_not_the_raw_instruction():
    from policyforge.edit.apply import apply_edit_plan

    plan = _plan()
    provider = ScriptedProvider(DOCUMENT.replace("quarterly", "monthly"))
    revised = apply_edit_plan(plan, DOCUMENT, provider)

    prompt = provider.calls[0]["prompt"]
    assert "Approved plan:" in prompt
    assert "Change review cadence from quarterly to monthly." in prompt
    assert "monthly" in revised


def test_apply_refuses_an_empty_plan():
    from policyforge.edit.apply import apply_edit_plan

    plan = _plan(steps=[], out_of_scope=["Already monthly."])
    with pytest.raises(ValueError, match="no steps"):
        apply_edit_plan(plan, DOCUMENT, ScriptedProvider())


def test_check_flags_a_dropped_framework_citation():
    """The single most damaging silent edit: a rewrite that reads fine but
    has lost the traceability an assessor relies on."""
    from policyforge.edit.apply import check_edit

    revised = DOCUMENT.replace(" [NIST AC-6]", "")
    check = check_edit(DOCUMENT, revised, plan=_plan())

    assert check.dropped_source_tags == ["[NIST AC-6]"]
    assert not check.is_clean


def test_check_flags_a_section_the_plan_did_not_ask_to_remove():
    from policyforge.edit.apply import check_edit

    revised = DOCUMENT.split("## Exceptions")[0]
    check = check_edit(DOCUMENT, revised, plan=_plan())

    assert "Exceptions" in check.removed_headings
    assert not check.is_clean


def test_check_accepts_a_removal_the_plan_did_ask_for():
    from policyforge.edit.apply import check_edit

    plan = _plan(
        steps=[
            {
                "kind": "remove",
                "target": "Exceptions",
                "summary": "Drop the exceptions section.",
                "rationale": "x",
            }
        ]
    )
    revised = DOCUMENT.split("## Exceptions")[0]
    check = check_edit(DOCUMENT, revised, plan=plan)

    assert check.removed_headings == []
    assert check.is_clean


def test_check_reports_an_unchanged_rewrite():
    from policyforge.edit.apply import check_edit

    check = check_edit(DOCUMENT, DOCUMENT, plan=_plan())
    assert check.unchanged
    assert check.is_clean


# --------------------------------------------------------------------------
# Macro round-trip safety
# --------------------------------------------------------------------------


def test_detects_macros_that_cannot_survive_the_round_trip():
    """storage -> markdown -> storage is lossless only for the `code` macro
    this project's own exporter emits."""
    from policyforge.edit.apply import detect_unsupported_macros

    storage = (
        '<p>Text</p><ac:structured-macro ac:name="code"><ac:plain-text-body>'
        "<![CDATA[x]]></ac:plain-text-body></ac:structured-macro>"
        '<ac:structured-macro ac:name="expand"/>'
        '<ac:structured-macro ac:name="panel"/>'
    )

    assert detect_unsupported_macros(storage) == ["expand", "panel"]


def test_a_page_of_plain_html_has_no_unsupported_macros():
    from policyforge.edit.apply import detect_unsupported_macros

    assert detect_unsupported_macros("<h1>Title</h1><p>Body</p>") == []


# --------------------------------------------------------------------------
# The CLI, where the safety gates live
# --------------------------------------------------------------------------


def _patch_confluence(monkeypatch, *, storage_body, published=None, version=3):
    """Fake the Confluence fetch/publish pair; record any publish attempt."""
    import policyforge.cli as cli_mod
    from policyforge.export.confluence_importer import ConfluencePage

    page = ConfluencePage(
        id="123",
        title="Access Control Standard",
        version=version,
        storage_body=storage_body,
        webui_url="https://x.atlassian.net/wiki/page",
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.fetch_confluence_page",
        lambda **kwargs: page,
    )

    attempts = published if published is not None else []

    def _update(markdown_text, **kwargs):
        attempts.append({"markdown": markdown_text, **kwargs})
        return "https://x.atlassian.net/wiki/page"

    monkeypatch.setattr("policyforge.export.confluence_exporter.update_page_body", _update)
    return cli_mod, attempts


def _run(monkeypatch, tmp_path, *extra_args, storage_body=None, responses=None):
    published: list = []
    cli_mod, published = _patch_confluence(
        monkeypatch,
        storage_body=storage_body if storage_body is not None else "<h1>Access Control</h1>",
        published=published,
    )
    provider = ScriptedProvider(
        *(responses or (_plan_json(), DOCUMENT.replace("quarterly", "monthly")))
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.confluence_to_markdown", lambda html: DOCUMENT
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-confluence",
            "--instruction",
            "Make reviews monthly.",
            "--space",
            "ENG",
            "--title",
            "Access Control Standard",
            "--host",
            "https://x.atlassian.net/wiki",
            "--out-dir",
            str(tmp_path / "edits"),
            "--history-dir",
            str(tmp_path / "history"),
            *extra_args,
        ],
    )
    return result, published, provider


def test_dry_run_is_the_default_and_publishes_nothing(tmp_path, monkeypatch):
    """The safety property that matters most: running the command without
    --apply must never modify Confluence."""
    result, published, _ = _run(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert published == []
    assert "Dry run" in result.output
    assert "Re-run with --apply" in result.output
    assert (tmp_path / "edits" / "access-control-standard.md").exists()


def test_apply_publishes_against_the_version_it_planned_against(tmp_path, monkeypatch):
    result, published, _ = _run(monkeypatch, tmp_path, "--apply", "--yes")

    assert result.exit_code == 0, result.output
    assert len(published) == 1
    assert published[0]["expected_version"] == 3
    assert published[0]["page_id"] == "123"
    assert "monthly" in published[0]["markdown"]


def test_apply_without_yes_asks_before_publishing(tmp_path, monkeypatch):
    published: list = []
    cli_mod, published = _patch_confluence(
        monkeypatch, storage_body="<h1>Access Control</h1>", published=published
    )
    provider = ScriptedProvider(_plan_json(), DOCUMENT.replace("quarterly", "monthly"))
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.confluence_to_markdown", lambda html: DOCUMENT
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-confluence",
            "--instruction",
            "Make reviews monthly.",
            "--space",
            "ENG",
            "--title",
            "Access Control Standard",
            "--host",
            "https://x.atlassian.net/wiki",
            "--apply",
            "--out-dir",
            str(tmp_path / "edits"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
        input="n\n",
    )

    assert result.exit_code != 0  # aborted
    assert published == []


def test_refuses_a_page_whose_macros_would_be_destroyed(tmp_path, monkeypatch):
    result, published, _ = _run(
        monkeypatch,
        tmp_path,
        "--apply",
        "--yes",
        storage_body='<ac:structured-macro ac:name="panel"/>',
    )

    assert result.exit_code != 0
    assert published == []
    assert "cannot round-trip" in result.output
    assert "panel" in result.output


def test_allow_macros_proceeds_but_says_what_will_be_lost(tmp_path, monkeypatch):
    result, _, _ = _run(
        monkeypatch,
        tmp_path,
        "--allow-macros",
        storage_body='<ac:structured-macro ac:name="panel"/>',
    )

    assert result.exit_code == 0, result.output
    assert "has unsupported macros: panel" in result.output


def test_an_empty_plan_stops_before_rewriting(tmp_path, monkeypatch):
    """No steps means no second LLM call and nothing written."""
    result, published, provider = _run(
        monkeypatch,
        tmp_path,
        responses=(_plan_json(steps=[], out_of_scope=["Already monthly."]),),
    )

    assert result.exit_code == 0, result.output
    assert published == []
    assert "Nothing to apply" in result.output
    assert len(provider.calls) == 1  # planner only


def test_citation_loss_is_reported_before_the_publish_prompt(tmp_path, monkeypatch):
    result, _, _ = _run(
        monkeypatch,
        tmp_path,
        responses=(_plan_json(), DOCUMENT.replace(" [NIST AC-6]", "")),
    )

    assert result.exit_code == 0, result.output
    assert "framework citations present before are missing after" in result.output
    assert "[NIST AC-6]" in result.output


def test_the_before_state_is_recorded_even_on_a_dry_run(tmp_path, monkeypatch):
    """So there is always a local copy to restore from, even if the run is
    abandoned partway."""
    from policyforge.history.version_store import load_history

    _run(monkeypatch, tmp_path)

    records = load_history(tmp_path / "history", "confluence/access-control-standard")
    assert [r.source for r in records] == ["confluence-edit-before"]


def test_publishing_records_the_after_state_too(tmp_path, monkeypatch):
    from policyforge.history.version_store import load_history

    _run(monkeypatch, tmp_path, "--apply", "--yes")

    records = load_history(tmp_path / "history", "confluence/access-control-standard")
    assert [r.source for r in records] == ["confluence-edit-before", "confluence-edit-after"]


def test_a_concurrent_edit_is_reported_not_clobbered(tmp_path, monkeypatch):
    """Someone else edited the page while this one was being planned. The
    write must fail loudly rather than silently discard their change."""
    import policyforge.cli as cli_mod
    from policyforge.export.confluence_exporter import ConcurrentEditError
    from policyforge.export.confluence_importer import ConfluencePage

    page = ConfluencePage(
        id="123",
        title="Access Control Standard",
        version=3,
        storage_body="<h1>Access Control</h1>",
        webui_url="https://x.atlassian.net/wiki/page",
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.fetch_confluence_page", lambda **kw: page
    )

    def _conflict(markdown_text, **kwargs):
        raise ConcurrentEditError("Page is at version 4, but the edit was planned against 3.")

    monkeypatch.setattr("policyforge.export.confluence_exporter.update_page_body", _conflict)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda config: ScriptedProvider(_plan_json(), DOCUMENT.replace("quarterly", "monthly")),
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.confluence_to_markdown", lambda html: DOCUMENT
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-confluence",
            "--instruction",
            "Make reviews monthly.",
            "--space",
            "ENG",
            "--title",
            "Access Control Standard",
            "--host",
            "https://x.atlassian.net/wiki",
            "--apply",
            "--yes",
            "--out-dir",
            str(tmp_path / "edits"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )

    assert result.exit_code != 0
    assert "version 4" in result.output


# --------------------------------------------------------------------------
# edit-topic: one instruction across a topic's whole document set
# --------------------------------------------------------------------------


POLICY_DOC = """# Access Control Policy

## Purpose

Access to systems is granted on a least-privilege basis.

## Policy Statements

- Access is granted only to those with a documented need.
"""

PROCEDURE_DOC = """# Access Review Procedure

## Purpose

Steps for performing the periodic access review.

## Procedure Steps

1. Export the account list quarterly. [NIST AC-2]
"""

TOPIC_BODIES = {
    "Access Control Policy": POLICY_DOC,
    "Access Control Standard": DOCUMENT,
    "Access Review Procedure": PROCEDURE_DOC,
}


def _procedure_plan_json():
    """A plan aimed at the Procedure's own heading.

    The planner demotes any step naming a heading the document doesn't have,
    so a Procedure plan cannot reuse the Standard's "Requirements" target.
    """
    return _plan_json(
        steps=[
            {
                "kind": "modify",
                "target": "Procedure Steps",
                "summary": "Run the account export monthly instead of quarterly.",
                "rationale": "The instruction asks for monthly reviews.",
            }
        ]
    )


def _registry(tmp_path, *, pages=None, space="ENG"):
    """Write a one-topic registry, optionally without a `confluence:` block."""
    if pages is None:
        pages = {
            "policy": "Access Control Policy",
            "standard": "Access Control Standard",
            "procedure": "Access Review Procedure",
        }
    lines = [
        "topics:",
        "  - name: Access Review",
        "    owner: IAM Engineering",
        "    nist_controls: [AC-2]",
    ]
    if space and pages:
        lines.append("    confluence:")
        lines.append(f"      space: {space}")
        lines.append("      pages:")
        lines += [f'        {tier}: "{title}"' for tier, title in pages.items()]
    path = tmp_path / "topics.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _patch_multi_page(monkeypatch, bodies):
    """Fake fetch/publish for several pages at once, keyed by page title.

    The storage body is a marker naming the page; `confluence_to_markdown` is
    faked to turn that marker back into the right markdown, which keeps each
    page's content distinct all the way through the run.
    """
    import policyforge.cli as cli_mod
    from policyforge.export.confluence_importer import ConfluencePage

    ids = {title: f"id-{index}" for index, title in enumerate(bodies)}

    def _fetch(*, space, title, host, **kwargs):
        return ConfluencePage(
            id=ids[title],
            title=title,
            version=3,
            storage_body=f"<!--{title}-->",
            webui_url=f"https://x.atlassian.net/wiki/{ids[title]}",
        )

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.confluence_to_markdown",
        lambda html: bodies[html.removeprefix("<!--").removesuffix("-->")],
    )

    published: list = []

    def _update(markdown_text, **kwargs):
        published.append({"markdown": markdown_text, **kwargs})
        return f"https://x.atlassian.net/wiki/{kwargs['page_id']}"

    monkeypatch.setattr("policyforge.export.confluence_exporter.update_page_body", _update)
    return cli_mod, published


def _run_topic(monkeypatch, tmp_path, *extra_args, responses=(), registry=None):
    cli_mod, published = _patch_multi_page(monkeypatch, TOPIC_BODIES)
    provider = ScriptedProvider(*responses)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"model": "fake-model"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-topic",
            "--instruction",
            "Access reviews move from quarterly to monthly.",
            "--topic-name",
            "Access Review",
            "--topics",
            str(registry if registry is not None else _registry(tmp_path)),
            "--host",
            "https://x.atlassian.net/wiki",
            "--out-dir",
            str(tmp_path / "edits"),
            "--history-dir",
            str(tmp_path / "history"),
            *extra_args,
        ],
    )
    return result, published, provider


def test_edit_topic_plans_every_page_but_only_rewrites_the_ones_with_edits(tmp_path, monkeypatch):
    """A cadence change belongs in the Standard and the Procedure. The Policy
    planning comes back empty, and that page must then be left completely
    alone rather than having an edit forced into it."""
    result, published, provider = _run_topic(
        monkeypatch,
        tmp_path,
        responses=(
            _plan_json(steps=[], out_of_scope=["Cadence is a Standard-tier detail."]),
            _plan_json(),
            _procedure_plan_json(),
            DOCUMENT.replace("quarterly", "monthly"),
            PROCEDURE_DOC.replace("quarterly", "monthly"),
        ),
    )

    assert result.exit_code == 0, result.output
    assert published == []  # dry run is still the default here
    assert len(provider.calls) == 5  # three plans, two rewrites
    assert "no edits planned — leaving unchanged" in result.output
    assert (tmp_path / "edits" / "access-control-standard.md").exists()
    assert (tmp_path / "edits" / "access-review-procedure.md").exists()
    assert not (tmp_path / "edits" / "access-control-policy.md").exists()


def test_edit_topic_tells_each_planner_which_tier_it_is_reading(tmp_path, monkeypatch):
    """The planner can only decide that a change doesn't belong in the Policy
    if it is told which tier it is looking at."""
    result, _, provider = _run_topic(
        monkeypatch,
        tmp_path,
        responses=(_plan_json(steps=[]), _plan_json(steps=[]), _plan_json(steps=[])),
    )

    assert result.exit_code == 0, result.output
    assert "No edits proposed for any page" in result.output
    prompts = [call["prompt"] for call in provider.calls]
    assert sum("POLICY tier" in p for p in prompts) == 1
    assert sum("STANDARD tier" in p for p in prompts) == 1
    assert sum("PROCEDURE tier" in p for p in prompts) == 1
    # Each planner is also told the siblings exist, so it doesn't duplicate an
    # edit that belongs in one of them.
    assert all("one of a set covering the same topic" in p for p in prompts)


def test_edit_topic_publishes_the_whole_set_on_apply(tmp_path, monkeypatch):
    result, published, _ = _run_topic(
        monkeypatch,
        tmp_path,
        "--apply",
        "--yes",
        responses=(
            _plan_json(steps=[]),
            _plan_json(),
            _procedure_plan_json(),
            DOCUMENT.replace("quarterly", "monthly"),
            PROCEDURE_DOC.replace("quarterly", "monthly"),
        ),
    )

    assert result.exit_code == 0, result.output
    assert sorted(p["title"] for p in published) == [
        "Access Control Standard",
        "Access Review Procedure",
    ]
    assert all(p["expected_version"] == 3 for p in published)
    assert all("monthly" in p["markdown"] for p in published)


def test_edit_topic_can_be_narrowed_to_one_tier(tmp_path, monkeypatch):
    result, _, provider = _run_topic(
        monkeypatch,
        tmp_path,
        "--tiers",
        "standard",
        responses=(_plan_json(), DOCUMENT.replace("quarterly", "monthly")),
    )

    assert result.exit_code == 0, result.output
    assert "1 page(s) in ENG" in result.output
    assert len(provider.calls) == 2  # one plan, one rewrite


def test_edit_topic_refuses_a_topic_that_declares_no_pages(tmp_path, monkeypatch):
    """Guessing at a page title would edit whatever page happened to match."""
    result, published, _ = _run_topic(
        monkeypatch, tmp_path, registry=_registry(tmp_path, pages={}, space="")
    )

    assert result.exit_code != 0
    assert "no `confluence:` block" in result.output
    assert published == []


def test_edit_topic_lists_the_known_topics_when_the_name_is_wrong(tmp_path, monkeypatch):
    cli_mod, published = _patch_multi_page(monkeypatch, TOPIC_BODIES)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: ScriptedProvider())

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-topic",
            "--instruction",
            "Move reviews to monthly.",
            "--topic-name",
            "Acess Review",  # typo
            "--topics",
            str(_registry(tmp_path)),
            "--host",
            "https://x.atlassian.net/wiki",
        ],
    )

    assert result.exit_code != 0
    assert "Available: Access Review" in result.output
    assert published == []


def test_edit_topic_reports_exactly_what_landed_when_one_publish_fails(tmp_path, monkeypatch):
    """Confluence has no cross-page transaction. If the second page loses a
    race, the run must say which page is already updated — silently exiting
    would leave the set inconsistent with nobody knowing."""
    from policyforge.export.confluence_exporter import ConcurrentEditError

    cli_mod, published = _patch_multi_page(monkeypatch, TOPIC_BODIES)

    def _update(markdown_text, **kwargs):
        if kwargs["title"] == "Access Review Procedure":
            raise ConcurrentEditError("Page moved to version 5 while this edit was prepared.")
        published.append({"markdown": markdown_text, **kwargs})
        return "https://x.atlassian.net/wiki/ok"

    monkeypatch.setattr("policyforge.export.confluence_exporter.update_page_body", _update)
    provider = ScriptedProvider(
        _plan_json(steps=[]),
        _plan_json(),
        _procedure_plan_json(),
        DOCUMENT.replace("quarterly", "monthly"),
        PROCEDURE_DOC.replace("quarterly", "monthly"),
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-topic",
            "--instruction",
            "Access reviews move to monthly.",
            "--topic-name",
            "Access Review",
            "--topics",
            str(_registry(tmp_path)),
            "--host",
            "https://x.atlassian.net/wiki",
            "--apply",
            "--yes",
            "--out-dir",
            str(tmp_path / "edits"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )

    assert result.exit_code != 0
    assert [p["title"] for p in published] == ["Access Control Standard"]
    assert "FAILED Access Review Procedure (procedure)" in result.output
    assert "1 page(s) were published before this failed" in result.output


# --------------------------------------------------------------------------
# Change tracking: is the plan recoverable after the terminal scrolls away?
# --------------------------------------------------------------------------


def test_a_dry_run_leaves_the_plan_on_disk_beside_the_revision(tmp_path, monkeypatch):
    result, _, _ = _run(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output

    saved = json.loads(
        (tmp_path / "edits" / "access-control-standard.plan.json").read_text(encoding="utf-8")
    )
    assert saved["instruction"] == "Make reviews monthly."
    assert saved["steps"][0]["target"] == "Requirements"
    assert saved["steps"][0]["kind"] == "modify"


def test_the_published_record_carries_the_plan_the_model_and_the_version(tmp_path, monkeypatch):
    """The changelog has to answer *why* a page changed — including what was
    flagged for judgement and what was declined — not just what the diff was."""
    from policyforge.history.version_store import load_history

    cli_mod, published = _patch_confluence(monkeypatch, storage_body="<h1>Access Control</h1>")
    provider = ScriptedProvider(
        _plan_json(
            risks=["A monthly cadence increases reviewer workload."],
            out_of_scope=["Left the Policy alone; cadence is a Standard-tier detail."],
        ),
        DOCUMENT.replace("quarterly", "monthly"),
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"model": "fake-model"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.confluence_to_markdown", lambda html: DOCUMENT
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-confluence",
            "--instruction",
            "Make reviews monthly.",
            "--space",
            "ENG",
            "--title",
            "Access Control Standard",
            "--host",
            "https://x.atlassian.net/wiki",
            "--tier",
            "standard",
            "--apply",
            "--yes",
            "--out-dir",
            str(tmp_path / "edits"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(published) == 1

    records = load_history(tmp_path / "history", "confluence/access-control-standard")
    after = records[-1]
    assert after.source == "confluence-edit-after"
    assert after.metadata["model"] == "fake-model"
    assert after.metadata["tier"] == "standard"
    assert after.metadata["page_version"] == 4  # the version this run created

    plan = after.metadata["plan"]
    assert plan["instruction"] == "Make reviews monthly."
    assert plan["steps"][0]["kind"] == "modify"
    assert plan["risks"] == ["A monthly cadence increases reviewer workload."]
    assert plan["out_of_scope"] == ["Left the Policy alone; cadence is a Standard-tier detail."]


def test_history_can_read_back_the_confluence_stream_and_render_the_plan(tmp_path, monkeypatch):
    """`edit-confluence` records under a `confluence/` slug. Until --tier
    accepted that value, the command wrote history nobody could read back."""
    from policyforge.cli import cli
    from policyforge.history.version_store import record_version

    history_dir = tmp_path / "history"
    record_version(
        history_dir,
        "confluence/acs",
        "# A\n\nReviewed quarterly.\n",
        source="confluence-edit-before",
        metadata={"title": "Access Control Standard", "page_version": 3},
    )
    record_version(
        history_dir,
        "confluence/acs",
        "# A\n\nReviewed monthly.\n",
        source="confluence-edit-after",
        metadata={
            "title": "Access Control Standard",
            "page_version": 4,
            "plan": {
                "instruction": "Make reviews monthly.",
                "steps": [
                    {
                        "kind": "modify",
                        "target": "Requirements",
                        "summary": "quarterly -> monthly",
                        "rationale": "The instruction asks for it.",
                    }
                ],
                "risks": ["Increases reviewer workload."],
                "out_of_scope": ["Left the Policy alone."],
            },
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "history",
            "--tier",
            "confluence",
            "--name",
            "acs",
            "--history-dir",
            str(history_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "confluence-edit-before" in result.output
    assert "asked: Make reviews monthly." in result.output
    assert "[modify] Requirements: quarterly -> monthly" in result.output
    assert "! flagged: Increases reviewer workload." in result.output
    assert "~ not done: Left the Policy alone." in result.output

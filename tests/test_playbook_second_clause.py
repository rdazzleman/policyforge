"""A Playbook sentence has one main clause, NIST's (#323, 80's ruling (B)).

`playbook_obligations` passed "NIST suggests reviewing the inventory, and Acme
will adopt it": the subject check reads the start, and `classify` does not
count "will" as binding. 1d found it reviewing #322.

80's first ruling -- any clause after a joiner must begin with NIST -- was
measured before building and refused 65 of b5's 66 real glm sentences,
because glm writes lists ("...; establishing ...", "..., and stakeholder
engagement plans"). It was withdrawn. The rule now refuses an ORGANIZATION
actor after a joiner: a closed generic list, plus the organization's own name,
teams and vendors from its config.

**The real corpus:** `fixtures/playbook_glm_run301_sentences.md` holds every
Playbook-only sentence from b5's stored #301 glm Standards (run301 and
run301b, 5 files, 66 sentences), one per paragraph, with any tag that sat on
the next line appended. Replayed here with no model call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.content.deontic import GENERIC_ACTORS, analyze, playbook_obligations

CORPUS = Path(__file__).parent / "fixtures" / "playbook_glm_run301_sentences.md"
TAG = "[NIST AI RMF Playbook Govern 1.4 Action 1]"
ACME = ("Acme Health", "Acme")


def _flagged(sentence: str, org_actors=()) -> bool:
    return len(playbook_obligations(f"{sentence} {TAG}\n", org_actors)) == 1


# -- first: the real output (the test the withdrawn ruling would have failed) ----


def test_the_real_corpus_is_66_playbook_sentences():
    text = CORPUS.read_text(encoding="utf-8")
    assert sum(s.cites_only_the_playbook for s in analyze(text)) == 66


@pytest.mark.parametrize(
    "org_actors",
    [(), ACME, (*ACME, "IAM Engineering", "SecOps", "Okta", "Jira")],
    ids=["generic-only", "org-name", "org-name-teams-vendors"],
)
def test_the_real_corpus_raises_no_false_alarm(org_actors):
    """b5's glm Standards were written for "Acme Health"; a real config would
    declare it, so the corpus is replayed with it declared as well as without."""
    text = CORPUS.read_text(encoding="utf-8")
    assert playbook_obligations(text, org_actors) == []


# -- each joiner: an organization actor fails, NIST passes -----------------------

JOINERS = [", and ", ", but ", ", so ", "; ", "; and ", " \u2014 ", "\u2014"]


@pytest.mark.parametrize("joiner", JOINERS)
@pytest.mark.parametrize(
    "actor",
    ["the organization will adopt it", "we will adopt it", "the team is responsible for it"],
)
def test_a_generic_actor_after_a_joiner_fails(joiner, actor):
    assert _flagged(f"NIST suggests reviewing the inventory{joiner}{actor}.")


@pytest.mark.parametrize("joiner", JOINERS)
def test_the_organizations_own_name_after_a_joiner_fails(joiner):
    """1d's sentence, with Acme declared in config."""
    assert _flagged(f"NIST suggests reviewing the inventory{joiner}Acme will adopt it.", ACME)


@pytest.mark.parametrize("joiner", JOINERS)
@pytest.mark.parametrize(
    "clause",
    ["the Playbook lists three actions", "NIST also recommends a quarterly review"],
)
def test_nist_or_the_playbook_after_a_joiner_passes(joiner, clause):
    assert not _flagged(f"NIST suggests reviewing the inventory{joiner}{clause}.", ACME)


@pytest.mark.parametrize(
    "sentence",
    [
        # 80's named cases.
        "NIST suggests reviewing the inventory, but we must keep it current.",
        "NIST suggests reviewing the inventory; the organization will record each review.",
        # Not "will": the verb does not matter, the actor does.
        "NIST suggests reviewing the inventory, and Acme is accountable for it.",
        "NIST suggests reviewing the inventory, and our staff sign off on it.",
        # The opener shapes from #319 do not change this.
        "For Govern 1.4, NIST suggests reviewing the inventory, and Acme will adopt it.",
    ],
)
def test_the_named_commitments_fail(sentence):
    assert _flagged(sentence, ACME)


def test_a_declared_team_name_fails():
    teams = ("IAM Engineering",)
    assert _flagged("NIST suggests reviewing access, and IAM  Engineering will do so.", teams)
    assert not _flagged("NIST suggests reviewing access, and IAM Engineering will do so.")


def test_a_multi_word_name_matches_across_any_whitespace():
    """`analyze` collapses whitespace before this rule sees a sentence, so
    through `playbook_obligations` spacing never varies; `framed_as_nists` is
    public, and called directly a wrapped or double-spaced name must match."""
    from policyforge.content.deontic import framed_as_nists

    for spacing in ("  ", "\n", "\t"):
        sentence = f"NIST suggests reviewing access, and IAM{spacing}Engineering will do so."
        assert not framed_as_nists(sentence, (), ("IAM Engineering",)), repr(spacing)


@pytest.mark.parametrize(
    "sentence",
    [
        "NIST suggests reviewing the inventory, and The Organization will adopt it.",
        "NIST suggests reviewing the inventory; WE will adopt it.",
        "NIST suggests reviewing the inventory, AND the team will adopt it.",
    ],
)
def test_generic_actors_and_joiners_match_in_any_case(sentence):
    assert _flagged(sentence, ACME)


# One-word team and vendor names that are also ordinary words (1d on #329).
ONE_WORD_ORG = ("Security", "Compliance", "IT", "Legal", "Privacy")


def test_one_word_config_names_raise_no_false_alarm_on_real_text():
    """Case-insensitive, these fired on 3 of the 66 real sentences and 4 of
    the 459 shipped Playbook actions, all Oxford-comma list items ("..., and
    legal review"). Config names are proper nouns, so they match as written."""
    import json

    from policyforge.content.deontic import _second_clause_actor

    assert playbook_obligations(CORPUS.read_text(encoding="utf-8"), ONE_WORD_ORG) == []
    catalog = Path(__file__).resolve().parent.parent / "data" / "frameworks"
    rows = json.loads(
        (catalog / "nist-ai-rmf-playbook" / "controls.json").read_text(encoding="utf-8")
    )
    actions = [e["description"] for r in rows for e in r["enhancements"]]
    assert len(actions) == 459
    assert [a for a in actions if _second_clause_actor(a, ONE_WORD_ORG)] == []


def test_a_one_word_config_name_is_still_caught_as_written():
    assert _flagged("NIST suggests reviewing access, and Security will do so.", ONE_WORD_ORG)
    assert not _flagged("NIST suggests reviewing access, and security reviews.", ONE_WORD_ORG)


def test_a_config_name_ending_in_punctuation_matches():
    assert _flagged("NIST suggests reviewing access, and Foo (EU) will do so.", ("Foo (EU)",))


def test_the_zardoz_check_skill_reads_the_org_name_from_its_config(tmp_path):
    """The second caller of `check_tree`: `/check` in the shell must pass the
    session's config, or it would miss what `policyforge check` catches."""
    from policyforge.zardoz.shell import ShellState
    from policyforge.zardoz.skills import _check

    tree = tmp_path / "docs"
    tree.mkdir()
    (tree / "ai-standard.md").write_text(
        "# AI Standard\n\nNIST suggests reviewing the inventory, and Globex will adopt it. "
        f"{TAG}\n",
        encoding="utf-8",
    )
    declared = _check(ShellState(config={"org": {"name": "Globex"}}, content_dir=tree), [])
    other = _check(ShellState(config={"org": {"name": "Initech"}}, content_dir=tree), [])
    assert "Globex will adopt it" in declared
    assert "Globex will adopt it" not in other


def test_coordinated_verbs_with_no_new_subject_pass():
    assert not _flagged("NIST suggests reviewing and updating the inventory.", ACME)
    assert not _flagged("NIST suggests reviewing the inventory and documenting changes.", ACME)


@pytest.mark.parametrize(
    "sentence",
    [
        # "we", "us" and "our" are words, not prefixes.
        "NIST suggests reviewing the inventory, and weekly reports on it.",
        "NIST suggests reviewing the inventory, and users of each system.",
        "NIST suggests reviewing the inventory, and ourselves-as-auditors checklists.",
        # "management" is left out on purpose: a list item in the real corpus.
        "NIST suggests allocating budget, and management resources for review.",
    ],
)
def test_a_word_that_only_starts_like_an_actor_passes(sentence):
    assert not _flagged(sentence, ACME)


# -- the stated limit, shown rather than hidden -----------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        # An actor neither generic nor declared.
        "NIST suggests reviewing the inventory, and Globex will adopt it.",
        # A declared name in another case: config names match as written.
        "NIST suggests reviewing the inventory, and ACME will adopt it.",
        # A clause joined some other way than the listed joiners.
        "NIST suggests reviewing the inventory, Acme will adopt it.",
        "NIST suggests reviewing the inventory, which the organization will adopt.",
    ],
)
def test_the_stated_limit_passes(sentence):
    """If one of these starts failing, the rule was widened and the comment
    beside `_second_clause_actor` must change too."""
    assert not _flagged(sentence, ACME)


def test_the_generic_list_is_closed_and_leaves_out_management():
    assert "management" not in GENERIC_ACTORS
    assert {"the organization", "the organisation", "we", "our", "the team"} <= set(GENERIC_ACTORS)


# -- the organization's actors come from its config ---------------------------------


def test_org_actors_are_derived_from_config():
    from policyforge.org.context import org_actors

    config = {
        "org": {
            "name": "Acme Health",
            "vendors": {"identity_provider": "Okta", "ticketing": "Jira"},
            "teams": {"identity_access": "IAM Engineering", "security_operations": "  "},
        }
    }
    assert set(org_actors(config)) == {"Acme Health", "Okta", "Jira", "IAM Engineering"}
    assert org_actors({"org": {"vendors": ["Splunk", ""]}}) == ("Splunk",)
    assert org_actors({}) == ()


def test_org_actors_include_names_under_keys_no_role_recognises():
    """9b on #329: `load_org_profile` sends an unrecognised key, or an
    unkeyed team list, to `unknown` (the KEYS, not the names). Reading the
    actors from it dropped them, and "..., and IT Ops will adopt it" passed."""
    from policyforge.org.context import load_org_profile, org_actors

    config = {
        "org": {
            "name": "Acme",
            "teams": {"it_ops": "IT Ops", "identity_access": "IAM"},
            "vendors": {"mystery_tool": "Zendesk"},
        }
    }
    assert set(load_org_profile(config).unknown) == {"it_ops", "mystery_tool"}  # the premise
    assert set(org_actors(config)) == {"Acme", "IT Ops", "IAM", "Zendesk"}
    assert set(org_actors({"org": {"teams": ["Platform", "Data"]}})) == {"Platform", "Data"}
    # Only strings are names: a number or a nested mapping is not an actor.
    odd = {"org": {"name": "Initech", "teams": {"a": 5, "b": {"x": 1}, "c": "Globex"}}}
    assert org_actors(odd) == ("Initech", "Globex")
    assert org_actors({"org": "Acme"}) == ("Acme",)
    assert org_actors({"org": ["Acme"]}) == ()
    assert _flagged("NIST suggests reviewing access, and IT Ops will adopt it.", org_actors(config))


@pytest.mark.own_config
@pytest.mark.parametrize(
    "org_yaml, caught",
    [
        ("org: Globex\n", True),  # a bare string is the organization's name
        ('org: ["Globex"]\n', False),  # not a mapping: no actors, and no crash
        ("org:\n  name: Initech\n  teams: {a: 5, b: {x: 1}, c: Globex}\n", True),
        ("org:\n", False),
    ],
)
def test_check_survives_any_org_shape(tmp_path, monkeypatch, org_yaml, caught):
    """1d on #329: `check` never read `org:` before #323, and `org: Acme`
    turned the pre-publish gate into an AttributeError traceback. Every
    shape now exits without one; only string names become actors."""
    from click.testing import CliRunner

    from policyforge.cli import cli

    tree = tmp_path / "docs"
    tree.mkdir()
    (tree / "a.md").write_text(
        f"# A\n\nNIST suggests reviewing the inventory, and Globex will adopt it. {TAG}\n",
        encoding="utf-8",
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(org_yaml, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["check", "--content-dir", str(tree)])
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(
        result.exception
    )
    assert "Traceback" not in result.output
    assert ("Globex will adopt it" in result.output) is caught, result.output


@pytest.mark.own_config
def test_policyforge_check_reads_the_org_name_from_config(tmp_path, monkeypatch):
    """On the user's path: `policyforge check` with a config naming the
    organization refuses "..., and <Org> will adopt it"; the same tree with no
    org in config does not (the generic list has no company names)."""
    from click.testing import CliRunner

    from policyforge.cli import cli

    tree = tmp_path / "docs"
    tree.mkdir()
    (tree / "ai-standard.md").write_text(
        "# AI Standard\n\nNIST suggests reviewing the inventory, and Globex will adopt it. "
        f"{TAG}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()

    (tmp_path / "config" / "config.yaml").write_text("org:\n  name: Globex\n", encoding="utf-8")
    declared = CliRunner().invoke(cli, ["check", "--content-dir", str(tree)])
    assert "Globex will adopt it" in declared.output, declared.output

    (tmp_path / "config" / "config.yaml").write_text("org:\n  name: Initech\n", encoding="utf-8")
    other = CliRunner().invoke(cli, ["check", "--content-dir", str(tree)])
    assert "Globex will adopt it" not in other.output, other.output

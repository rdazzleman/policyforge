"""`generate` lists unresolved citations apart from roles left to fill (#222).

A bracket `generate` leaves in place was always reported as a role, with the
advice "Add them under `org.vendors` or `org.teams`". For a bracket that is a
framework citation the run's catalogs don't resolve, that advice is wrong. Now
the two are separate lists, each with its own advice, and together they are
every bracket left (`outstanding`, unchanged).

The split is the citation checker's own (80's ruling on #222): whether a
bracket resolves is `is_citation_tag` against the run's `CatalogScope` (#221),
and whether it is written as a citation is `SOURCE_TAG_RE`, or a framework
named alone. The expected sets come from the other side of the check: the
bundled catalogs, the roles `org.roles` declares, and hand-written shapes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.content.tags import is_unresolved_citation
from policyforge.ingest.schema import load_controls
from policyforge.org.context import OrgProfile, apply_substitutions
from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES
from policyforge.topics.satisfies import catalog_scope

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
CORPUS = sorted((ROOT / "tests" / "fixtures" / "deontic_corpus").rglob("*.txt"))
STARTER = ("nist-800-53-r5", "fedramp", "arc-ampe", "hipaa-security-rule")
AI = (*STARTER, "nist-ai-rmf", "nist-ai-rmf-playbook")


def _scope(*directories: str):
    return catalog_scope(
        [c for d in directories for c in load_controls(FRAMEWORKS / d / "controls.json")]
    )


@pytest.fixture(scope="module")
def only_800_53():
    return _scope("nist-800-53-r5")


# ---- 1d's hand-written cases (3666), and the issue's three shapes ----------

UNRESOLVED_CITATIONS = [
    "[ISO 27001 A.5.1]",  # a framework with no catalog on disk
    "[NIST ZZ-99]",  # a real framework, a missing id
    "[NIST AI RMF Govern 1.1]",  # a catalog outside an 800-53-only run (#221)
    "[NIST]",  # a framework named alone: an incomplete citation (80, 3512)
    "[FedRAMP]",
    "[HIPAA]",
]
ROLES_BY_HAND = [
    "[CISO]",
    "[IT]",
    "[Security Lead]",
    "[Ticketing System]",
    "[ISO]",  # a framework with no catalog on disk can't be recognised alone
    "[NIS]",  # not a word-boundary prefix of `NIST` (resolve_framework's own example)
]


@pytest.mark.parametrize("span", UNRESOLVED_CITATIONS)
def test_an_unresolved_citation_is_a_citation_not_a_role(span, only_800_53):
    assert is_unresolved_citation(span, only_800_53), span


@pytest.mark.parametrize("span", ROLES_BY_HAND)
def test_a_role_stays_a_role(span, only_800_53):
    assert not is_unresolved_citation(span, only_800_53), span


ROLES = [role.label for role in (*TEAM_ROLES.values(), *VENDOR_ROLES.values())]


@pytest.mark.parametrize("label", ROLES)
def test_every_declared_role_stays_a_role(label, only_800_53):
    assert not is_unresolved_citation(f"[{label}]", only_800_53), label
    assert not is_unresolved_citation(f"[{label}]"), label


def test_a_resolved_citation_is_in_neither_list(only_800_53):
    """`[NIST SC-8]` with 800-53 as the run's only NIST catalog resolves, so it
    is neither a role nor an unresolved citation."""
    assert not is_unresolved_citation("[NIST SC-8]", only_800_53)
    result = apply_substitutions("Per [NIST SC-8].", OrgProfile(), only_800_53)
    assert result.outstanding == [] and result.unresolved_citations == []


def test_the_two_lists_partition_what_is_left(only_800_53):
    text = (
        "Owned by [CISO] per [NIST SC-8], [ISO 27001 A.5.1], [NIST ZZ-99] and [NIST];"
        " tickets go to [Ticketing System]."
    )
    result = apply_substitutions(text, OrgProfile(), only_800_53)
    assert result.unresolved_citations == ["ISO 27001 A.5.1", "NIST ZZ-99", "NIST"]
    # Since #228 (80's ruling 3918): `[CISO]` is not a declared role label, so
    # it is unfilled, not a role; `[Ticketing System]` is declared.
    assert result.roles_left == ["Ticketing System"]
    assert result.unfilled == ["CISO"]
    assert sorted(result.roles_left + result.unfilled + result.unresolved_citations) == sorted(
        result.outstanding
    )


# ---- the corpus: conservation, and extent ----------------------------------


@pytest.fixture(scope="module")
def scopes():
    return {"starter": _scope(*STARTER), "ai": _scope(*AI), "800-53": _scope("nist-800-53-r5")}


def _corpus(scope_for):
    results = []
    for path in CORPUS:
        text = path.read_text(encoding="utf-8")
        results.append(apply_substitutions(text, OrgProfile(), scope_for(text)))
    return results


def test_the_split_conserves_every_bracket_and_the_roles_dont_move(scopes):
    """In every corpus document, the two lists partition `outstanding`. And
    the roles left don't depend on the run's catalogs: only the citation list
    grows when a run is drawn from fewer catalogs than its documents cite."""
    own = _corpus(lambda text: scopes["ai"] if "[NIST AI RMF" in text else scopes["starter"])
    narrow = _corpus(lambda text: scopes["800-53"])
    disk = _corpus(lambda text: None)
    assert len(own) == len(CORPUS) > 100  # extent: the corpus was read
    for results in (own, narrow, disk):
        for r in results:
            parts = r.roles_left + r.unfilled + r.unresolved_citations
            assert sorted(parts) == sorted(r.outstanding)
    roles = [sum(len(r.roles_left) + len(r.unfilled) for r in rs) for rs in (own, narrow, disk)]
    assert roles[0] == roles[1] == roles[2], roles
    cited = [sum(len(r.unresolved_citations) for r in rs) for rs in (own, narrow, disk)]
    # every catalog <= own set < 800-53 alone. The disk resolves at least what
    # a document's own set does: since 6044 on #17 a short `NIST` tag names
    # 800-53 there too, where it was ambiguous before.
    assert cited[2] <= cited[0] < cited[1], cited
    assert cited[1] > 500, cited  # measured 1139: out-of-set citations an 800-53 run reports


# ---- through `generate`, the user's path ------------------------------------

SYNTHESIS = (
    "---\ntopic: Access Control\nderived_from:\n- nist-800-53-r5\n---\n\n"
    "- Staff must review access. [NIST 800-53 AC-2]\n"
)
STANDARD = "# Access Control Standard\n\nStaff must review access. [NIST 800-53 AC-2]\n"
DRAFT = (
    "# Access Control Policy\n\n"
    "The [CISO] must review access [NIST 800-53 AC-2] [ISO 27001 A.5.1] [NIST ZZ-99].\n"
)


class _Draft:
    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=DRAFT, model="fake")

    def check(self):
        return True


def test_generate_prints_two_lists_with_their_own_advice(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from policyforge.llm import ledger

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(SYNTHESIS, encoding="utf-8")
    standard = tmp_path / "standard.md"
    standard.write_text(STANDARD, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"org": {"name": "Acme"}, "llm": {"provider": "local"}}
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: ledger.wrap(_Draft(), config))
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "policy",
            "--synthesis",
            str(synthesis),
            "--standard",
            str(standard),
            "--out",
            str(tmp_path / "out" / "access-control.md"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    # Since #228, `[CISO]` (not a declared role label) is on the unfilled line,
    # with the neutral advice, and there is no roles line for this draft.
    assert not any("role(s) left as placeholders" in line for line in lines)
    unfilled = next(i for i, line in enumerate(lines) if "left unfilled" in line)
    cites = next(i for i, line in enumerate(lines) if "citation(s) that don't resolve" in line)
    assert lines[unfilled] == "1 placeholder(s) left unfilled: CISO"
    assert "parameters ledger" in lines[unfilled + 1]
    assert lines[cites] == (
        "2 citation(s) that don't resolve against this run's catalogs: ISO 27001 A.5.1, NIST ZZ-99"
    )
    # 80's ruling 3707: the citation advice leads with the tag and the catalog,
    # and also covers a role written in citation shape. Pinned whole. The keys
    # are the ones config has: `org.vendors` and `org.teams` (there is no
    # `org.roles`).
    assert lines[cites + 1] == (
        "  Check each tag's spelling, or add that framework's catalog to the run, then "
        "regenerate. If one is a role, not a citation, add it under `org.vendors` or "
        "`org.teams`."
    )


# --- #266: a Procedure's Related Standard, in brackets, is its reference ---

PROCEDURE_DRAFT = (
    "# Access Control Procedure\n\n"
    "## Related Standard\n\n[Access Control Standard]\n\n"
    "## Steps\n\nRecord each review in the [Unknown System].\n"
)


class _ProcedureDraft(_Draft):
    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=PROCEDURE_DRAFT, model="fake")


def test_generate_writes_the_related_standard_without_reporting_it(tmp_path, monkeypatch):
    """Through the real `generate`: the Standard's own title in brackets is
    written plain and isn't reported, while a real placeholder beside it still
    is. This pins the wiring, `generate` passing the title it read from
    `--standard`, which the unit tests of `apply_substitutions` can't."""
    import policyforge.cli as cli_mod
    from policyforge.llm import ledger

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(SYNTHESIS, encoding="utf-8")
    standard = tmp_path / "standard.md"
    standard.write_text(STANDARD, encoding="utf-8")
    out = tmp_path / "out" / "access-control.md"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"org": {"name": "Acme"}, "llm": {"provider": "local"}}
    )
    monkeypatch.setattr(
        cli_mod, "get_provider", lambda config: ledger.wrap(_ProcedureDraft(), config)
    )
    result = CliRunner().invoke(
        cli_mod.cli,
        ["generate", "--tier", "procedure", "--synthesis", str(synthesis),
         "--standard", str(standard), "--out", str(out), "--history-dir", str(tmp_path / "h")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    written = out.read_text(encoding="utf-8")
    assert "\nAccess Control Standard\n" in written and "[Access Control Standard]" not in written
    unfilled = next(line for line in result.output.splitlines() if "left unfilled" in line)
    assert unfilled == "1 placeholder(s) left unfilled: Unknown System"

"""The placeholder reader resolves a bracket against the run's catalog set,
as `satisfies --controls` does (#215, 80's ruling 3525).

After #214 the reader resolved against every catalog on disk, where `NIST`
names five catalogs, so a short `[NIST SC-8]` was listed as an unfilled role
while `satisfies --controls` with 800-53 alone counted it cited. One input,
not a second rule: both readers now ask one `CatalogScope`, built by one
`catalog_scope`, and `generate` passes the catalogs its synthesis was drawn
from.
"""

from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from policyforge.content.tags import SOURCE_TAG_RE, is_citation_tag
from policyforge.ingest.schema import load_controls
from policyforge.org.context import OrgProfile, apply_substitutions
from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES
from policyforge.topics.satisfies import catalog_scope, document_evidence

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
CORPUS = sorted((ROOT / "tests" / "fixtures" / "deontic_corpus").rglob("*.txt"))


def _controls(*directories: str) -> list:
    return [c for d in directories for c in load_controls(FRAMEWORKS / d / "controls.json")]


#: The catalogs the deontic corpus was generated over (the 20 starter topics
#: over NIST, FedRAMP, ARC-AMPE and HIPAA; `synthesis/merge.py`), and with the
#: AI RMF and its Playbook for an AI topic, which can't be synthesized without
#: them (its anchors are AI RMF ids; the Playbook reaches a Standard only when
#: it's loaded).
STARTER = ("nist-800-53-r5", "fedramp", "arc-ampe", "hipaa-security-rule")
AI = (*STARTER, "nist-ai-rmf", "nist-ai-rmf-playbook")


@pytest.fixture(scope="module")
def only_800_53():
    return catalog_scope(_controls("nist-800-53-r5"))


@pytest.fixture(scope="module")
def starter():
    return catalog_scope(_controls(*STARTER))


@pytest.fixture(scope="module")
def ai():
    return catalog_scope(_controls(*AI))


# ---- 80's three tests ------------------------------------------------------


def test_with_800_53_as_the_sets_only_nist_catalog_a_short_tag_is_a_citation(only_800_53):
    assert is_citation_tag("[NIST SC-8]", only_800_53)


def test_with_no_set_given_a_short_tag_names_800_53():
    """Every catalog on disk holds five NIST catalogs, and `NIST` alone still
    names 800-53 (80's ruling 6044 on #17, replacing 3512's ambiguity for
    this name): the citation form a Standard writes. Another NIST catalog's
    id under the bare name is still reported."""
    assert is_citation_tag("[NIST SC-8]")
    assert not is_citation_tag("[NIST 03.01.01]")


def test_a_second_nist_catalog_in_the_set_leaves_nist_naming_800_53():
    """With 800-171 in the run, `NIST SC-8` is still 800-53's, and 800-171's
    own id under the bare name is reported, not read as 800-171's: adding a
    catalog can't turn an unknown into evidence (6044 on #17)."""
    both = catalog_scope(_controls("nist-800-53-r5", "nist-800-171-r3"))
    assert is_citation_tag("[NIST SC-8]", both)
    assert is_citation_tag("[NIST 800-53 SC-8]", both)
    assert not is_citation_tag("[NIST 03.01.01]", both)


def _corpus_spans() -> list[str]:
    spans: set[str] = set()
    for path in CORPUS:
        text = path.read_text(encoding="utf-8")
        spans.update(m.group(0) for m in re.finditer(r"\[[^\[\]\n]{1,80}\]", text))
    return sorted(s for s in spans if SOURCE_TAG_RE.fullmatch(s))


@pytest.mark.parametrize("directories", [("nist-800-53-r5",), STARTER, AI], ids=len)
def test_satisfies_and_the_placeholder_reader_agree_on_every_corpus_tag(directories):
    """For one set, the citation checker's real path (`document_evidence`)
    and the placeholder reader give the same answer for every source-shaped
    bracket in the 128-document corpus. The checker side is its own report,
    not the scope method both share, so this compares two readers."""
    from policyforge.topics.satisfies import _declared_family_links, _declared_relationships

    controls = _controls(*directories)
    scope = catalog_scope(controls)
    relationships, links = _declared_relationships(), _declared_family_links()
    spans = _corpus_spans()
    assert len(spans) > 2000, len(spans)  # extent: the corpus was read
    disagree = []
    for span in spans:
        document = SimpleNamespace(body=f"Access is reviewed. {span}\n", title="t", path="p.md")
        evidence = document_evidence(
            document,
            controls=controls,
            crosswalk={},
            declared_relationships=relationships,
            family_links=links,
        )
        if bool(evidence.cited) != is_citation_tag(span, scope):
            disagree.append(span)
    assert disagree == []


# ---- what done means (1d's allocation, 3564) -------------------------------


def _outstanding(scope_for) -> list[str]:
    labels: list[str] = []
    for path in CORPUS:
        text = path.read_text(encoding="utf-8")
        labels += apply_substitutions(text, OrgProfile(), scope_for(text)).outstanding
    return labels


_SHORT_NIST_RE = re.compile(r"\[(NIST [A-Z]{2}-\d+(?:\(\d+\))?)\]")


def test_the_corpus_short_nist_tags_resolve_on_disk_and_with_each_documents_set(starter, ai):
    """9b's 28 labels (#214) were short NIST tags listed as roles against
    every catalog on disk, where `NIST` was ambiguous. Since 6044 on #17
    `NIST` names 800-53, so none is left either way, and reading each
    document against its own set leaves nothing the disk resolves."""
    short = {m for path in CORPUS for m in _SHORT_NIST_RE.findall(path.read_text(encoding="utf-8"))}
    assert len(short) > 20, sorted(short)  # the corpus carries them
    disk = _outstanding(lambda text: None)
    run = _outstanding(lambda text: ai if "[NIST AI RMF" in text else starter)
    assert not short & set(disk), sorted(short & set(disk))
    assert not short & set(run), sorted(short & set(run))
    assert set(run) <= set(disk), sorted(set(run) - set(disk))


ROLES = [role.label for role in (*TEAM_ROLES.values(), *VENDOR_ROLES.values())]


@pytest.mark.parametrize("label", ROLES)
def test_every_declared_role_stays_a_role_under_a_runs_set(label, ai):
    assert not is_citation_tag(f"[{label}]", ai), label


def test_80s_table_still_holds_under_a_runs_set(ai):
    """80's table (3462), read against a run's set rather than the disk."""
    text = "Owned by [CISO], [IT], [Security Lead] and [CISO - interim]."
    result = apply_substitutions(text, OrgProfile(name="X", industry="Y"), ai)
    assert result.outstanding == ["CISO", "IT", "Security Lead"]


def test_a_regulatory_standard_cited_above_its_paragraphs_is_a_citation(starter):
    """`[HIPAA 164.316(b)]` is a standard whose paragraphs the catalog
    carries. `satisfies` counted it cited (80's ruling on #423); the reader
    listed it as a role until both asked the one scope."""
    assert is_citation_tag("[HIPAA 164.316(b)]", starter)
    assert is_citation_tag("[HIPAA 164.316(b)]")


# ---- the run: generate passes its synthesis's set -----------------------------


class _Draft:
    def __init__(self, text: str):
        self.text = text

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


DRAFT = (
    "# Access Control Standard\n\nStaff must review access. [NIST SC-8]\n\n"
    "Staff must control access. [HIPAA Security Rule 164.312(a)(1)]\n\nOwned by [CISO].\n"
)


def _generate(tmp_path, monkeypatch, frontmatter: str):
    import policyforge.cli as cli_mod
    from policyforge.llm import ledger

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(
        f"---\ntopic: Access Control\n{frontmatter}---\n\n- Req. [NIST 800-53 SC-8]\n",
        encoding="utf-8",
    )
    config = {"org": {"name": "Acme", "industry": "Health"}, "llm": {"provider": "local"}}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: ledger.wrap(_Draft(DRAFT), c))
    return CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--synthesis",
            str(synthesis),
            "--out",
            str(tmp_path / "standards" / "access-control.md"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )


def _left(output: str) -> str:
    """Every bracket `generate` reports left: since #222, roles and unresolved
    citations on separate lines, and since #228 an unfilled line too (an
    unresolved `[NIST SC-8]` is on the citation line)."""
    markers = ("left as placeholders", "left unfilled", "citation(s) that don't resolve")
    return "\n".join(line for line in output.splitlines() if any(m in line for m in markers))


def test_generate_reads_brackets_against_its_synthesiss_catalogs(tmp_path, monkeypatch):
    result = _generate(tmp_path, monkeypatch, "derived_from:\n- nist-800-53-r5\n")
    assert result.exit_code == 0, result.output
    assert "Citations read against the synthesis's catalogs: nist-800-53-r5." in result.output
    assert "CISO" in _left(result.output), result.output
    assert "NIST SC-8" not in _left(result.output), result.output
    # HIPAA is outside this synthesis's set, so its citation is reported.
    assert "HIPAA Security Rule 164.312(a)(1)" in _left(result.output), result.output


def test_generate_with_no_recorded_set_reads_every_catalog_on_disk(tmp_path, monkeypatch):
    """The control case: a synthesis written before `derived_from` travelled.
    Every catalog on disk includes HIPAA, so its citation resolves; the set
    is what tells this from the recorded-set case above, since `NIST SC-8`
    resolves either way (6044 on #17)."""
    result = _generate(tmp_path, monkeypatch, "")
    assert result.exit_code == 0, result.output
    assert "Citations read against every catalog on disk" in result.output
    assert "HIPAA Security Rule 164.312(a)(1)" not in _left(result.output), result.output
    assert "NIST SC-8" not in _left(result.output), result.output


def test_generate_falls_back_whole_when_a_recorded_catalog_is_not_on_disk(tmp_path, monkeypatch):
    """A partial set would read a citation of the missing catalog as a role,
    so the whole set falls back to the disk, and the line names the gap."""
    result = _generate(
        tmp_path, monkeypatch, "derived_from:\n- nist-800-53-r5\n- hitrust-controls.json\n"
    )
    assert result.exit_code == 0, result.output
    assert "names hitrust-controls.json, which is not a catalog on disk" in result.output
    # Read against the disk, so HIPAA resolves though the record named 800-53.
    assert "HIPAA Security Rule 164.312(a)(1)" not in _left(result.output), result.output

"""An invented shorthand id cannot downgrade the Playbook gate (#340).

1d, reviewing #337: under #333's rule an inherited part counted as the
Playbook only if it resolved, so `[NIST AI RMF Playbook Govern 1.1 Action 1 |
Govern 9.9 Action 1]` -- an action NIST never published -- made the sentence
mixed, the gate never read it, and "Acme will adopt ..." fell from an ERROR
to a strength warning. No finding named `Govern 9.9`.

80's ruling, (b) with (a) as its reporting half:
- (b) inheritance is decided by known framework names -- ONE list, derived
  from every catalog on disk and shared with `satisfies` -- and an inherited
  Playbook part keeps the sentence Playbook-only whether or not it resolves;
- (a) `check` reports the unresolvable inherited part as its own ERROR.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from policyforge.content import tags
from policyforge.content.deontic import analyze, unresolved_playbook_parts

TAG = "[NIST AI RMF Playbook Govern 1.1 Action 1"
CATALOGS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


@pytest.fixture
def fresh_names():
    tags.reset_known_framework_names()
    yield
    tags.reset_known_framework_names()


def _check(sentence: str):
    from policyforge.content.check import check_tree

    root = Path(tempfile.mkdtemp())
    (root / "standards").mkdir()
    (root / "standards" / "a.md").write_text(f"# A\n\n{sentence}\n", encoding="utf-8")
    return [f.message for f in check_tree(root).findings if f.severity == "error"]


# -- 80's four tests, through the real `policyforge check` path --------------------------


def test_the_invented_id_is_the_gate_error_and_its_own_error():
    errors = _check(f"Acme will adopt the inventory. {TAG} | Govern 9.9 Action 1]")
    assert any("not framed as NIST's" in e for e in errors), errors
    named = [
        e for e in errors if "Govern 9.9 Action 1" in e and "no such NIST AI RMF Playbook" in e
    ]
    assert len(named) == 1, errors


def test_the_same_sentence_without_the_invented_part_is_unchanged():
    errors = _check(f"Acme will adopt the register. {TAG}]")
    assert len(errors) == 1 and "not framed as NIST's" in errors[0]
    assert unresolved_playbook_parts(f"x {TAG}]") == []


def test_a_part_naming_800_53_after_a_playbook_part_is_read_as_800_53():
    (statement,) = analyze(f"NIST suggests reviewing the inventory. {TAG} | NIST 800-53 AC-2]\n")
    assert not statement.cites_only_the_playbook
    assert unresolved_playbook_parts(f"x {TAG} | NIST 800-53 AC-2]") == []


def test_a_real_mixed_playbook_and_800_53_sentence_keeps_its_exemption():
    """Mixed, it answers to the strength rule, not the Playbook gate."""
    errors = _check(f"The organization shall review access. {TAG} | NIST 800-53 AC-2]")
    assert not any(
        "not framed as NIST's" in e or "no such NIST AI RMF Playbook" in e for e in errors
    )


# -- 1d's measures: one list, derived, shared ---------------------------------------------


def test_the_name_list_is_derived_from_the_catalogs_on_disk(fresh_names):
    names = tags.known_framework_names()
    for directory in sorted(CATALOGS.iterdir()):
        controls = directory / "controls.json"
        if controls.exists():
            declared = json.loads(controls.read_text(encoding="utf-8"))[0]["framework"]
            assert declared in names, (directory.name, declared)
    assert "NIST AI RMF Playbook" in names


def test_the_gate_and_satisfies_read_the_same_name_list(monkeypatch, fresh_names):
    """Replace the one source and both readers change."""
    from policyforge.content import deontic
    from policyforge.topics.satisfies import parse_citations

    calls = []
    real = tags.known_framework_names

    def spy():
        calls.append(1)
        return real()

    monkeypatch.setattr(tags, "known_framework_names", spy)
    deontic._parts(f"{TAG} | Govern 1.1 Action 2]")
    parse_citations(f"x {TAG} | Govern 1.1 Action 2]\n", ["NIST AI RMF Playbook"])
    assert len(calls) == 2


def test_known_names_and_declared_keys_read_the_same_catalogs_the_same_way(tmp_path):
    """9b on #346: the names a tag part is matched against (#340) and the
    names a key is declared for (#295) come from one walk, so they cannot
    drift. Over the shipped tree -- where every catalog declares a key -- and
    over a BYOC root beside it, every known name has a declaration and every
    declaration is a known name."""
    import warnings

    from policyforge.frameworks.registry import declared_keys, known_framework_names

    byoc = tmp_path / "hitrust-csf"
    byoc.mkdir()
    (byoc / "framework.yaml").write_text(
        "name: HITRUST CSF v11\nframework_id: hitrust-csf\n", encoding="utf-8"
    )
    row = {
        "control_id": "01.a",
        "title": "t",
        "framework": "HITRUST CSF",
        "framework_version": "11",
    }
    (byoc / "controls.json").write_text(json.dumps([row]), encoding="utf-8")

    for roots in ([CATALOGS], [CATALOGS, tmp_path]):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            keys = declared_keys(roots=roots)
        known = {" ".join(n.lower().split()) for n in known_framework_names(roots=roots)}
        assert known == set(keys), known ^ set(keys)
    assert {"hitrust csf", "hitrust csf v11"} <= known


def test_a_framework_the_user_brings_is_known_once_its_catalog_is_on_disk(
    tmp_path, monkeypatch, fresh_names
):
    """Without a HITRUST catalog, `HITRUST CSF 01.a` names nothing known and
    inherits the Playbook -- loudly, as an unresolved Playbook part. With the
    catalog under `frameworks/`, the name is known and the part is HITRUST's."""
    orphan = unresolved_playbook_parts(f"x {TAG} | HITRUST CSF 01.a]")
    assert orphan == [(1, "NIST AI RMF Playbook HITRUST CSF 01.a")]

    catalog = tmp_path / "frameworks" / "hitrust-csf"
    catalog.mkdir(parents=True)
    row = {
        "control_id": "01.a",
        "title": "t",
        "framework": "HITRUST CSF",
        "framework_version": "11",
    }
    (catalog / "controls.json").write_text(json.dumps([row]), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    tags.reset_known_framework_names()

    (statement,) = analyze(f"NIST suggests reviewing access. {TAG} | HITRUST CSF 01.a]\n")
    assert not statement.cites_only_the_playbook
    assert unresolved_playbook_parts(f"x {TAG} | HITRUST CSF 01.a]") == []


@pytest.mark.own_config
def test_a_malformed_config_is_named_and_names_are_still_read(tmp_path, monkeypatch, fresh_names):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("org: [unclosed\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.warns(UserWarning, match="config.yaml could not be read"):
        names = tags.known_framework_names()
    assert "NIST 800-53" in names  # the bundled catalogs, still

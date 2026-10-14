"""A catalog envelope's licence fails closed (#320, CR-24; 80's ruling).

The envelope marker was matched as written: `{"licence": "licensed"}` was
licensed, and `Licensed`, `LICENSED`, `licenced` or the key `license` read as
NOT licensed, fail-open on the boundary. Now any envelope value but an
explicit public one is licensed, by one helper (`ingest.schema.is_licensed`)
that the boundary and `publish_guard`'s L4 both call. A plain list declares
nothing, as before, and writing is unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from policyforge.frameworks import registry
from policyforge.ingest import schema
from policyforge.llm import boundary

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_guard  # noqa: E402

ROWS = [{"control_id": "x.1", "title": "t", "framework": "Pub", "framework_version": "1"}]

#: Written from the finding and the ruling, not from the code's own constant.
LICENSED_SPELLINGS = ["licensed", "Licensed", "LICENSED", "licenced", " licensed ", "", "other"]
PUBLIC_SPELLINGS = ["public-domain", " Public-Domain ", "PUBLIC-DOMAIN"]


@pytest.mark.parametrize("value", LICENSED_SPELLINGS)
def test_any_envelope_value_but_an_explicit_public_one_is_licensed(value):
    assert schema.is_licensed(value)


@pytest.mark.parametrize("value", PUBLIC_SPELLINGS)
def test_an_explicit_public_value_is_not(value):
    assert not schema.is_licensed(value)


def test_a_plain_list_declares_nothing():
    assert schema.parse_catalog(json.dumps(ROWS)) == (ROWS, None)
    assert not schema.is_licensed(None)


@pytest.mark.parametrize(
    ("envelope", "licensed"),
    [
        ({"licence": "Licensed", "controls": ROWS}, True),
        ({"license": "licensed", "controls": ROWS}, True),  # the American key
        ({"controls": ROWS}, True),  # an envelope that declares nothing
        ({"licence": "public-domain", "controls": ROWS}, False),
        # The one input that shows the `license` key is READ: failing closed,
        # an unread key would read as no value, so as licensed, for every
        # licensed spelling alike. Only its public value tells them apart.
        ({"license": "public-domain", "controls": ROWS}, False),
        # Both keys (ba on PR 333): public only when every one says public.
        ({"licence": "public-domain", "license": "licensed", "controls": ROWS}, True),
        ({"licence": "licensed", "license": "public-domain", "controls": ROWS}, True),
        ({"licence": "public-domain", "license": "Public-Domain", "controls": ROWS}, False),
    ],
)
def test_the_reader_hands_the_rule_what_the_envelope_says(envelope, licensed):
    _, licence = schema.parse_catalog(json.dumps(envelope))
    assert schema.is_licensed(licence) is licensed


def test_the_public_value_is_the_registrys():
    """One public value for manifests and envelopes alike."""
    assert registry.PUBLIC_DOMAIN in schema.PUBLIC_LICENCES


def test_writing_is_unchanged():
    assert json.loads(schema.catalog_json(ROWS, licence=schema.LICENSED_MARKER))["licence"] == (
        "licensed"
    )


# ---- the boundary ----------------------------------------------------------------------


@pytest.fixture
def public_dir(tmp_path, monkeypatch):
    """A public-domain framework directory: its path alone says public."""
    public = tmp_path / "frameworks" / "pub"
    public.mkdir(parents=True)
    (public / "framework.yaml").write_text("id: pub\nlicence: public-domain\n", encoding="utf-8")
    (tmp_path / "output").mkdir()
    monkeypatch.chdir(tmp_path)
    return public


@pytest.mark.parametrize(
    "envelope",
    [
        {"licence": "Licensed", "controls": ROWS},
        {"licence": "licenced", "controls": ROWS},
        {"license": "licensed", "controls": ROWS},
        {"controls": ROWS},
    ],
)
def test_the_boundary_classes_any_licensed_spelling_licensed(public_dir, envelope):
    path = public_dir / "controls.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert boundary.classify_path(Path("frameworks/pub/controls.json"), {}).klass == (
        boundary.LICENSED
    )


def test_an_envelope_that_says_public_keeps_its_paths_class(public_dir):
    path = public_dir / "controls.json"
    path.write_text(json.dumps({"licence": "public-domain", "controls": ROWS}), encoding="utf-8")
    assert boundary.classify_path(Path("frameworks/pub/controls.json"), {}).klass != (
        boundary.LICENSED
    )


# ---- publish_guard's L4 ----------------------------------------------------------------


def _l4(data: object) -> list[str]:
    findings = publish_guard.scan_contents([("x/controls.json", json.dumps(data).encode())], set())
    return [f.layer for f in findings]


@pytest.mark.parametrize(
    "envelope",
    [
        {"licence": "Licensed", "controls": ROWS},
        {"licence": "LICENSED", "controls": ROWS},
        {"license": "licensed", "controls": ROWS},
        {"controls": ROWS},
        {"licence": "licensed"},  # no controls: still a licensed marker
        {"licence": "public-domain", "license": "licensed", "controls": ROWS},
    ],
)
def test_l4_finds_every_licensed_spelling(envelope):
    assert _l4(envelope) == ["L4 data"]


@pytest.mark.parametrize(
    "data",
    [
        {"licence": "public-domain", "controls": ROWS},
        {"name": "x", "license": "MIT"},  # a package.json, not a catalog
        ROWS,  # a plain list with no licensed framework named
    ],
)
def test_l4_leaves_what_is_not_a_licensed_catalog(data):
    assert _l4(data) == []


def test_l4_reads_the_rule_from_this_checkouts_schema():
    """Not a copy: the guard's module is this tree's `ingest/schema.py`."""
    assert Path(publish_guard._SCHEMA.__file__).resolve() == (
        ROOT / "src" / "policyforge" / "ingest" / "schema.py"
    )

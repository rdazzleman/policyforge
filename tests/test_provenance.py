"""Provenance stamps on bundled framework catalogs.

The catalogs are fetched from moving upstreams and committed. Nothing
recorded which revision produced them, so an upstream revision and an
upstream compromise looked identical in a diff — both are just changed
requirement text.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from policyforge.ingest.oscal_loader import BASELINE_URLS, CATALOG_URL, OSCAL_REF
from policyforge.ingest.provenance import (
    MISMATCH,
    MISSING,
    UNSTAMPED,
    VERIFIED,
    content_digest,
    read_provenance,
    record_source_provenance,
    verify_all,
    verify_content,
)

EXISTING = """\
id: nist-800-53-r5
name: NIST SP 800-53 Rev 5
licence: public-domain
source: NIST OSCAL content repository, via policyforge etl-oscal
notes: >-
  A US government work, so freely redistributable.
"""


def test_the_oscal_fetch_is_pinned_to_a_named_revision():
    """`main` cannot be traced to anything a reviewer can look up."""
    assert OSCAL_REF != "main"
    assert OSCAL_REF.startswith("v")
    assert f"/{OSCAL_REF}/" in CATALOG_URL
    for url in BASELINE_URLS.values():
        assert f"/{OSCAL_REF}/" in url
        assert "/main/" not in url


def test_stamping_preserves_what_a_person_wrote(tmp_path):
    """The licence and the redistribution note are not the ETL's to restate."""
    path = tmp_path / "framework.yaml"
    path.write_text(EXISTING, encoding="utf-8")

    record_source_provenance(
        path, source_ref="v1.5.0", source_url=CATALOG_URL, content=b"catalog bytes"
    )

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["licence"] == "public-domain"
    assert data["name"] == "NIST SP 800-53 Rev 5"
    assert data["notes"].startswith("A US government work")
    assert data["source_ref"] == "v1.5.0"
    assert data["content_sha256"] == content_digest(b"catalog bytes")


def test_stamping_is_idempotent_apart_from_the_date(tmp_path):
    path = tmp_path / "framework.yaml"
    path.write_text(EXISTING, encoding="utf-8")

    first = record_source_provenance(
        path, source_ref="v1.5.0", source_url=CATALOG_URL, content=b"same"
    )
    second = record_source_provenance(
        path, source_ref="v1.5.0", source_url=CATALOG_URL, content=b"same"
    )
    assert first == second


def test_a_missing_framework_file_is_not_invented(tmp_path):
    """An ETL run pointed at a scratch directory should leave no orphan."""
    path = tmp_path / "nothing-here" / "framework.yaml"
    assert (
        record_source_provenance(path, source_ref="v1.5.0", source_url=CATALOG_URL, content=b"x")
        is None
    )
    assert not path.exists()


def test_reading_an_unstamped_framework_is_not_an_error(tmp_path):
    """Catalogs bundled before this existed have no stamp, and that is data."""
    path = tmp_path / "framework.yaml"
    path.write_text(EXISTING, encoding="utf-8")
    assert read_provenance(path) == {}


def test_verify_catches_a_catalog_that_no_longer_matches(tmp_path):
    (tmp_path / "framework.yaml").write_text(EXISTING, encoding="utf-8")
    (tmp_path / "controls.json").write_text('[{"id": "AC-2"}]', encoding="utf-8")
    record_source_provenance(
        tmp_path / "framework.yaml",
        source_ref="v1.5.0",
        source_url=CATALOG_URL,
        content=(tmp_path / "controls.json").read_bytes(),
    )
    assert verify_content(tmp_path).ok

    (tmp_path / "controls.json").write_text(
        '[{"id": "AC-2"}, {"id": "SNUCK-IN"}]', encoding="utf-8"
    )
    status = verify_content(tmp_path)
    assert status.state == MISMATCH
    assert not status.ok
    assert "does not match the recorded hash" in status.message


def test_an_unstamped_framework_is_not_a_pass(tmp_path):
    """The distinction another session caught, and the one that matters.

    Every catalog bundled before the stamps existed is UNSTAMPED, so a
    caller treating "nothing to check" as "checked" would report the whole
    bundled set as verified while verifying nothing. Not an error either —
    an unstamped catalog stays usable.
    """
    (tmp_path / "framework.yaml").write_text(EXISTING, encoding="utf-8")
    (tmp_path / "controls.json").write_text("[]", encoding="utf-8")

    status = verify_content(tmp_path)
    assert status.state == UNSTAMPED
    assert not status.ok
    assert not status.checkable
    assert "cannot be checked" in status.message


def test_the_bundled_catalogs_report_their_real_state():
    """Whatever it is, it must not be silence.

    Today they are unstamped, and this asserts the shape rather than the
    verdict so that stamping them is not a test failure.
    """
    statuses = verify_all(Path("data/frameworks"))
    assert statuses, "no framework directories found to check"
    assert all(s.state in {VERIFIED, UNSTAMPED, MISMATCH, MISSING} for s in statuses)
    assert not any(s.state == MISMATCH for s in statuses), [
        s.message for s in statuses if s.state == MISMATCH
    ]


def test_verify_reports_a_recorded_catalog_that_went_missing(tmp_path):
    (tmp_path / "framework.yaml").write_text(EXISTING, encoding="utf-8")
    (tmp_path / "controls.json").write_text("[]", encoding="utf-8")
    record_source_provenance(
        tmp_path / "framework.yaml",
        source_ref="v1.5.0",
        source_url=CATALOG_URL,
        content=b"[]",
    )
    (tmp_path / "controls.json").unlink()
    assert verify_content(tmp_path).state == MISSING


def test_a_second_pipeline_step_updates_the_hash_not_the_source(tmp_path):
    """The HIPAA pipeline is two commands writing one file.

    `etl-hipaa` fetches the regulation and stamps it; `etl-hipaa-crosswalk`
    rewrites the same controls.json with CPRT mappings attached. Without a
    restamp the mechanism would report MISMATCH on the one catalog that
    followed its own documented build steps.
    """
    from policyforge.ingest.provenance import restamp_content

    (tmp_path / "framework.yaml").write_text(EXISTING, encoding="utf-8")
    controls = tmp_path / "controls.json"
    controls.write_text('[{"control_id": "164.308"}]', encoding="utf-8")
    record_source_provenance(
        tmp_path / "framework.yaml",
        source_ref="2026-09-01",
        source_url="https://www.ecfr.gov/...",
        content=controls.read_bytes(),
    )
    assert verify_content(tmp_path).ok

    # The enrichment step rewrites the file.
    controls.write_text(
        '[{"control_id": "164.308", "source_crosswalk": {"nist-800-53": "RA-1"}}]', encoding="utf-8"
    )
    assert verify_content(tmp_path).state == MISMATCH

    restamp_content(tmp_path / "framework.yaml", content=controls.read_bytes())
    status = verify_content(tmp_path)
    assert status.ok
    recorded = read_provenance(tmp_path / "framework.yaml")
    assert recorded["source_ref"] == "2026-09-01"
    assert recorded["source_url"] == "https://www.ecfr.gov/..."


def test_enriching_an_unstamped_catalog_invents_nothing(tmp_path):
    """Nothing here knows which revision that text came from."""
    from policyforge.ingest.provenance import restamp_content

    (tmp_path / "framework.yaml").write_text(EXISTING, encoding="utf-8")
    (tmp_path / "controls.json").write_text("[]", encoding="utf-8")

    assert restamp_content(tmp_path / "framework.yaml", content=b"[]") is None
    assert verify_content(tmp_path).state == UNSTAMPED

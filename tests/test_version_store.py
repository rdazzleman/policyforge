"""history/version_store.py tests: recording, no-op dedup on identical
content, diffing, and reading history back."""

from __future__ import annotations

import pytest


def test_record_version_creates_first_version(tmp_path):
    from policyforge.history.version_store import load_history, record_version

    record = record_version(
        tmp_path,
        "standard/auth-mgmt",
        "# Title\n\nBody.\n",
        source="generate",
        metadata={"org": "Acme"},
    )

    assert record is not None
    assert record.version == 1
    assert record.source == "generate"
    assert record.lines_added > 0
    assert record.lines_removed == 0
    assert record.metadata == {"org": "Acme"}

    history = load_history(tmp_path, "standard/auth-mgmt")
    assert len(history) == 1
    assert history[0] == record


def test_record_version_skips_identical_content(tmp_path):
    from policyforge.history.version_store import load_history, record_version

    record_version(tmp_path, "standard/auth-mgmt", "# Title\n\nBody.\n", source="generate")
    second = record_version(tmp_path, "standard/auth-mgmt", "# Title\n\nBody.\n", source="generate")

    assert second is None
    assert len(load_history(tmp_path, "standard/auth-mgmt")) == 1


def test_record_version_skips_crlf_twin_of_lf_content(tmp_path):
    from policyforge.history.version_store import load_history, record_version

    record_version(tmp_path, "standard/auth", "line 1\nline 2\n", source="generate")
    second = record_version(tmp_path, "standard/auth", "line 1\r\nline 2\r\n", source="generate")

    assert second is None
    assert len(load_history(tmp_path, "standard/auth")) == 1


def test_record_version_skips_bare_cr_twin_of_lf_content(tmp_path):
    """`write_text_lf` folds a bare CR as well as CRLF, so the hash must too
    (ag's first fold handled CRLF only; ba measured +2/-2 for this twin)."""
    from policyforge.history.version_store import load_history, record_version

    record_version(tmp_path, "standard/auth", "line 1\nline 2\n", source="generate")
    second = record_version(tmp_path, "standard/auth", "line 1\rline 2\r", source="generate")

    assert second is None
    assert len(load_history(tmp_path, "standard/auth")) == 1


def _legacy_history(tmp_path, content: str) -> str:
    """A history as the store wrote it before the fold: the snapshot stored
    with LF (write_text_lf always did that), the hash taken of the raw text."""
    import hashlib
    import json

    raw_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    slug_dir = tmp_path / "standard" / "auth"
    slug_dir.mkdir(parents=True)
    (slug_dir / "v1.md").write_bytes(content.replace("\r\n", "\n").encode("utf-8"))
    (slug_dir / "v1.diff").write_bytes(b"")
    record = {
        "version": 1,
        "timestamp": "2026-10-01T00:00:00+00:00",
        "content_hash": raw_hash,
        "source": "generate",
        "lines_added": 2,
        "lines_removed": 0,
        "metadata": {},
    }
    (slug_dir / "index.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return raw_hash


@pytest.mark.parametrize("again", ["line 1\r\nline 2\r\n", "line 1\nline 2\n"], ids=["crlf", "lf"])
def test_a_history_hashed_before_the_fold_records_no_phantom(tmp_path, again):
    """The issue's "also state": an index entry hashed from CRLF text matches
    no folded hash, so the stored copy decides, and equal text records nothing."""
    from policyforge.history.version_store import load_history, record_version

    raw_hash = _legacy_history(tmp_path, "line 1\r\nline 2\r\n")

    assert record_version(tmp_path, "standard/auth", again, source="generate") is None
    history = load_history(tmp_path, "standard/auth")
    assert [r.content_hash for r in history] == [raw_hash]


def test_a_history_hashed_before_the_fold_still_records_a_real_change(tmp_path):
    from policyforge.history.version_store import load_history, record_version

    _legacy_history(tmp_path, "line 1\r\nline 2\r\n")

    third = record_version(tmp_path, "standard/auth", "line 1\nline 3\n", source="generate")

    assert third is not None and third.version == 2
    assert (third.lines_added, third.lines_removed) == (1, 1)
    assert len(load_history(tmp_path, "standard/auth")) == 2


def test_a_matching_hash_still_returns_before_reading_the_snapshot(tmp_path):
    """The stored-copy check comes after the hash, so a history whose latest
    snapshot file is gone answers an identical write as it did before."""
    from policyforge.history.version_store import record_version

    record_version(tmp_path, "standard/auth", "line 1\n", source="generate")
    (tmp_path / "standard" / "auth" / "v1.md").unlink()

    assert record_version(tmp_path, "standard/auth", "line 1\n", source="generate") is None


def test_record_version_bumps_version_and_computes_diff_stats_on_change(tmp_path):
    from policyforge.history.version_store import load_history, record_version

    record_version(tmp_path, "standard/auth-mgmt", "# Title\n\nOld body.\n", source="generate")
    second = record_version(
        tmp_path, "standard/auth-mgmt", "# Title\n\nNew body.\nExtra line.\n", source="generate"
    )

    assert second.version == 2
    assert second.lines_added == 2
    assert second.lines_removed == 1
    assert len(load_history(tmp_path, "standard/auth-mgmt")) == 2


def test_diff_versions_returns_unified_diff(tmp_path):
    from policyforge.history.version_store import diff_versions, record_version

    record_version(tmp_path, "standard/auth-mgmt", "line one\n", source="generate")
    record_version(tmp_path, "standard/auth-mgmt", "line one\nline two\n", source="generate")

    diff = diff_versions(tmp_path, "standard/auth-mgmt", 1, 2)

    assert "+line two" in diff
    assert "v1" in diff and "v2" in diff


def test_load_history_empty_when_nothing_recorded(tmp_path):
    from policyforge.history.version_store import load_history

    assert load_history(tmp_path, "standard/never-generated") == []


def test_record_version_tracks_multiple_slugs_independently(tmp_path):
    from policyforge.history.version_store import load_history, record_version

    record_version(tmp_path, "standard/auth-mgmt", "standard content\n", source="generate")
    record_version(tmp_path, "policy/auth-mgmt", "policy content\n", source="generate")

    assert len(load_history(tmp_path, "standard/auth-mgmt")) == 1
    assert len(load_history(tmp_path, "policy/auth-mgmt")) == 1


# ---- the store refuses a slug that leaves it ---------------------------


_ESCAPES = [
    "standard/../../outside",
    "../outside",
    "standard/..",
    "./standard/x",
    "/abs/outside",
    r"standard\..\..\outside",
    "C:/outside",
]


@pytest.mark.parametrize("slug", _ESCAPES)
def test_a_slug_that_leaves_the_store_is_refused_on_read_and_write(tmp_path, slug):
    """The CLI checks --name; Zardoz's /history did not, so the store checks too."""
    from policyforge.history.version_store import load_history, record_version

    store = tmp_path / "store" / ".history"
    store.mkdir(parents=True)
    with pytest.raises(ValueError, match="outside"):
        load_history(store, slug)
    with pytest.raises(ValueError, match="outside"):
        record_version(store, slug, "# Planted\n", source="generate")
    assert not list(tmp_path.rglob("*.md"))


def test_zardoz_history_cannot_read_an_index_outside_the_store(tmp_path):
    """Found by policyforge-ba: the skill's arguments are model-filled and reached
    the store unchecked, so a planted index.jsonl beside the store was readable."""
    import json
    from types import SimpleNamespace

    from policyforge.zardoz.skills import SKILLS

    store = tmp_path / "output" / ".history"
    store.mkdir(parents=True)
    outside = tmp_path / "output" / "secret"
    outside.mkdir()
    (outside / "index.jsonl").write_text(
        json.dumps(
            {
                "version": 7,
                "timestamp": "t",
                "content_hash": "h",
                "source": "planted",
                "lines_added": 1,
                "lines_removed": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = SKILLS["history"].run(SimpleNamespace(history_dir=store), ["standard", "../../secret"])

    assert "planted" not in report
    assert "Could not read history" in report


def test_a_confluence_title_slug_is_still_accepted(tmp_path):
    """Refusal is for traversal only; the confluence stream's own slugs still write."""
    from policyforge.history.version_store import load_history, record_version

    record_version(tmp_path, "confluence/access-control-standard-v2", "# A\n", source="edit")
    assert len(load_history(tmp_path, "confluence/access-control-standard-v2")) == 1

"""Tests for secret scanner before export and publish (#139).

Ensures high-confidence secret formats are detected, documented example
values and policy prose pass cleanly, publish/export-confluence refuse
unallowed findings (exit 2) without leaking secrets, and check reports
findings as warnings.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.cli import cli
from policyforge.content.check import check_tree
from policyforge.secrets import (
    ALLOWED_EXAMPLE_SECRETS,
    scan_line,
    scan_text,
)


def _jwt() -> str:
    """Build a synthetic JWT from parts at test time."""
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    pld = base64.urlsafe_b64encode(json.dumps({"sub": "user"}).encode()).decode().rstrip("=")
    sig = "sig" + "x" * 24
    return f"{hdr}.{pld}.{sig}"


# Synthetic secrets built from parts so gitleaks sees no literal credential
_PLANTED_SECRETS: list[tuple[str, str, str]] = [
    ("private key block", "-----" + "BEGIN RSA PRIVATE KEY-----", "----"),
    ("AWS access key ID", "AKIA" + "1234567890ABCDEF", "AKIA"),
    ("GitHub token", "ghp_" + "a" * 36, "ghp_"),
    ("GitHub token", "gho_" + "b" * 36, "gho_"),
    ("GitHub token", "ghs_" + "c" * 36, "ghs_"),
    ("GitHub token", "github_pat_" + "d" * 30, "gith"),
    ("Slack token", "xoxb-" + "1234567890-" + "abcdef123456", "xoxb"),
    ("OpenAI API key", "sk-" + "e" * 40, "sk-e"),
    ("OpenAI API key", "sk-proj-" + "f" * 40, "sk-p"),
    ("Anthropic API key", "sk-ant-" + "g" * 40, "sk-a"),
    ("Google API key", "AIza" + "h" * 35, "AIza"),
    ("JWT", _jwt(), "eyJh"),
]


@pytest.mark.parametrize("kind, secret, expected_prefix", _PLANTED_SECRETS)
def test_planted_secret_detected_with_kind_and_prefix(kind, secret, expected_prefix):
    findings = scan_line(f"api_key = {secret} # planted credential")
    assert len(findings) == 1
    found_kind, found_val = findings[0]
    assert found_kind == kind
    assert found_val == secret
    assert found_val[:4] == expected_prefix


def test_aws_documented_example_key_gives_no_finding():
    aws_example = "AKIAIOSFODNN7EXAMPLE"
    paired_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert aws_example in ALLOWED_EXAMPLE_SECRETS
    assert paired_secret in ALLOWED_EXAMPLE_SECRETS

    text = f"Example AWS Access Key: {aws_example}\nExample AWS Secret: {paired_secret}\n"
    findings = scan_text(text, filename="standards/cloud.md")
    assert findings == []


@pytest.mark.parametrize(
    "must_stay_line",
    [
        "Passwords must be at least 14 characters.",
        "Rotate API keys every 90 days.",
        "The `AKIA` prefix identifies AWS access keys.",
        "| Password expiration | 90 days |",
        "| Minimum length | 14 characters |",
        "| Complexity | 3 of 4 character classes |",
    ],
)
def test_must_stay_lines_give_no_findings(must_stay_line):
    findings = scan_line(must_stay_line)
    assert findings == []


def _standard(content_dir: Path, filename: str, secret: str) -> Path:
    std_dir = content_dir / "standards"
    std_dir.mkdir(parents=True, exist_ok=True)
    doc_path = std_dir / filename
    doc_path.write_text(
        f"""---
title: Access Control Standard
owner: Security
targets:
  confluence:
    space: SEC
    title: Access Control Standard
---

# Access Control Standard

<!-- [NIST AC-2] -->

Passwords must be at least 14 characters.
Rotate API keys every 90 days.
Planted credential: {secret}
""",
        encoding="utf-8",
    )
    return doc_path


@pytest.mark.parametrize("kind, secret, expected_prefix", _PLANTED_SECRETS)
def test_check_reports_secret_finding_as_warning_without_leaking_secret(
    tmp_path, kind, secret, expected_prefix
):
    _standard(tmp_path, "access-control.md", secret)
    report = check_tree(tmp_path)

    # Reported as warning, so report.ok is True and no errors
    assert report.ok
    secret_warnings = [w for w in report.warnings if kind in w.message]
    assert len(secret_warnings) == 1
    assert len(report.errors) == 0

    warning = secret_warnings[0]
    assert kind in warning.message
    assert expected_prefix in warning.message
    # Never leaks the full secret value
    assert secret not in str(warning)

    # CLI check passes (exit 0) and reports warning
    result = CliRunner().invoke(cli, ["check", "--content-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "warning(s):" in result.output
    assert kind in result.output
    assert expected_prefix in result.output
    assert secret not in result.output

    # With --strict, warnings cause failure (exit 1)
    strict_result = CliRunner().invoke(cli, ["check", "--content-dir", str(tmp_path), "--strict"])
    assert strict_result.exit_code == 1


@pytest.mark.parametrize("kind, secret, expected_prefix", _PLANTED_SECRETS)
def test_export_confluence_refuses_secret_finding(tmp_path, kind, secret, expected_prefix):
    doc_path = _standard(tmp_path, "access-control.md", secret)

    # Normal export-confluence refuses with exit 2, writing nothing
    result = CliRunner().invoke(
        cli,
        [
            "export-confluence",
            "--doc",
            str(doc_path),
            "--space",
            "SEC",
            "--title",
            "Access Control Standard",
            "--host",
            "https://wiki.example.com",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "Refusing to export" in result.output
    assert kind in result.output
    assert expected_prefix in result.output
    # Never prints matched secret
    assert secret not in result.output

    # With --allow-secret-findings, export proceeds and reports count allowed
    allowed_result = CliRunner().invoke(
        cli,
        [
            "export-confluence",
            "--doc",
            str(doc_path),
            "--space",
            "SEC",
            "--title",
            "Access Control Standard",
            "--host",
            "https://wiki.example.com",
            "--dry-run",
            "--allow-secret-findings",
        ],
    )
    assert allowed_result.exit_code == 0
    assert "Allowed 1 secret finding(s)." in allowed_result.output
    assert "Access Control Standard" in allowed_result.output


@pytest.mark.parametrize("kind, secret, expected_prefix", _PLANTED_SECRETS)
def test_publish_refuses_secret_finding(tmp_path, monkeypatch, kind, secret, expected_prefix):
    _standard(tmp_path, "access-control.md", secret)

    published_calls = []

    from policyforge.export.publisher import ConfluencePublisher

    class DummyPublisher(ConfluencePublisher):
        def fetch_for(self, doc):
            return None

        def write(self, doc):
            published_calls.append(doc)
            return "https://wiki.example.com/page"

    import policyforge.cli.content as content_cli

    monkeypatch.setattr(content_cli, "load_config_or_empty", lambda: {})
    monkeypatch.setattr(
        content_cli,
        "_publisher_for",
        lambda *a, **k: DummyPublisher(host="https://wiki.example.com"),
    )

    # publish refuses with exit 2 without calling publisher or writing anything
    result = CliRunner().invoke(
        cli,
        [
            "publish",
            "--content-dir",
            str(tmp_path),
            "--host",
            "https://wiki.example.com",
        ],
    )
    assert result.exit_code == 2
    assert "Refusing to publish" in result.output
    assert kind in result.output
    assert expected_prefix in result.output
    assert secret not in result.output
    assert len(published_calls) == 0

    # With --allow-secret-findings, publish proceeds and reports allowed count
    allowed_result = CliRunner().invoke(
        cli,
        [
            "publish",
            "--content-dir",
            str(tmp_path),
            "--host",
            "https://wiki.example.com",
            "--allow-secret-findings",
        ],
    )
    assert allowed_result.exit_code == 0
    assert "Allowed 1 secret finding(s)." in allowed_result.output


# ---- carried by ba (#139): shapes from the domain the first set didn't hold ----

# Each built from parts, as above. Written from what these credentials look
# like in the world, not from the patterns: four of them were missed before
# the carry.
_DOMAIN_SHAPES: list[tuple[str, str, str]] = [
    ("PGP private key block", "-----" + "BEGIN PGP PRIVATE KEY BLOCK-----", "private key block"),
    ("AWS temporary (STS) key", "AS" + "IA" + "Q3EGRCK7HXZ2WN4D", "AWS access key ID"),
    (
        "AWS key after an underscore",
        "AWS_KEY_" + "AK" + "IA" + "Q3EGRCK7HXZ2WN4D",
        "AWS access key ID",
    ),
    ("GitHub user-to-server token", "gh" + "u_" + "a1B2" * 9, "GitHub token"),
    ("GitHub refresh token", "gh" + "r_" + "a1B2" * 9, "GitHub token"),
    ("OpenAI legacy key", "sk" + "-" + "a1B2" * 12, "OpenAI API key"),
]


@pytest.mark.parametrize("name, secret, kind", _DOMAIN_SHAPES)
def test_a_credential_shape_from_the_domain_is_found(name, secret, kind):
    assert [k for k, _ in scan_line(f"value: {secret} end")] == [kind], name


@pytest.mark.parametrize(
    "line",
    [
        # A library's name in kebab-case reads as `sk-` and twenty word
        # characters: the old OpenAI pattern called it a key.
        "Use the sk-learn-compatible-pipeline-for-evaluations module.",
        "See the risk-based-access-control-requirements matrix.",
        "Cite NIST.SP.800-53r5 and docs.aws.amazon.com, e.g. U.S.C. 1.2.3.",
    ],
)
def test_technical_prose_gives_no_finding(line):
    assert scan_line(line) == []


def _publish_apply(tmp_path, monkeypatch, *extra):
    from policyforge.export.publisher import ConfluencePublisher

    written: list[str] = []

    class DummyPublisher(ConfluencePublisher):
        def fetch_for(self, doc):
            return None

        def write(self, doc):
            written.append(doc.relative_path)
            return "https://wiki.example.com/page"

    import policyforge.cli.content as content_cli

    monkeypatch.setattr(content_cli, "load_config_or_empty", lambda: {})
    monkeypatch.setattr(
        content_cli,
        "_publisher_for",
        lambda *a, **k: DummyPublisher(host="https://wiki.example.com"),
    )
    result = CliRunner().invoke(
        cli,
        [
            "publish",
            "--content-dir",
            str(tmp_path),
            "--host",
            "https://wiki.example.com",
            "--apply",
            *extra,
        ],
    )
    return result, written


def test_publish_with_apply_refuses_and_writes_nothing(tmp_path, monkeypatch):
    """The refusal is before any write, with --apply too: without it nothing
    is written anyway, so a plan-only run can't show that."""
    _standard(tmp_path, "access-control.md", "gh" + "p_" + "a1B2" * 9)

    result, written = _publish_apply(tmp_path, monkeypatch)

    assert result.exit_code == 2, result.output
    assert written == []


def test_a_draft_with_a_finding_does_not_block_publishing_the_rest(tmp_path, monkeypatch):
    """A draft has no destination, so it is never sent: its finding is
    check's warning, not a refusal of every other page in the run."""
    _standard(tmp_path, "access-control.md", "nothing to see")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "notes.md").write_text(
        "# Notes\n\nkey: " + "gh" + "p_" + "a1B2" * 9 + "\n", encoding="utf-8"
    )

    result, written = _publish_apply(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert written == ["standards/access-control.md"]
    assert "Refusing" not in result.output
    assert any("GitHub token" in w.message for w in check_tree(tmp_path).warnings)


def test_the_jwt_candidate_bounds_its_first_segment():
    """Refuse the primitive rather than time it. `\b` fires after every
    `-`, so an unbounded first segment makes each hyphen in a long run start
    a match that eats the run and backtracks: quadratic in the line (80k
    characters of "abc-abc-..." took 7 s; 9b, PR 403). A wall-clock bound
    would be the wrong instrument on a shared machine; the pattern's shape
    is what decides it."""
    try:
        from re import _parser as sre_parse  # Python 3.11+
    except ImportError:  # Python 3.10
        import sre_parse

    from policyforge.secrets import _JWT_CANDIDATE_RE

    parsed = sre_parse.parse(_JWT_CANDIDATE_RE.pattern)
    repeats = [arg for op, arg in parsed if op in (sre_parse.MAX_REPEAT, sre_parse.MIN_REPEAT)]
    low, high, _ = repeats[0]
    assert high != sre_parse.MAXREPEAT and high <= 4096, (low, high)


def test_a_long_hyphen_joined_token_is_scanned_and_gives_no_finding():
    """The input that was quadratic, at a size that finished in seconds even
    then: it must still be scanned, and it holds no credential."""
    assert scan_text("-".join(["abc"] * 15000)) == []
    assert scan_text("x." + "-".join(["abc"] * 15000)) == []

"""Scanner for credentials and secrets in generated or published documents.

High-confidence formats only: private key blocks, AWS access key IDs,
GitHub tokens, Slack tokens, OpenAI and Anthropic API keys, Google API keys,
and JSON Web Tokens (JWTs) with a decodable header that names `alg`.

Generic "password=" and entropy heuristics are excluded by design: policy
documents are full of password requirements and definitions, which must not
produce false positives.

Documented example credentials (such as AWS's documented example key pair)
are allowed by exact match.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from policyforge.content.tree import ContentDocument

#: Documented example credentials allowed by exact match.
#: Source: AWS Documentation ("Understanding and getting your AWS credentials",
#: https://docs.aws.amazon.com/general/latest/gr/aws-sec-cred-types.html)
ALLOWED_EXAMPLE_SECRETS: frozenset[str] = frozenset(
    {
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    }
)


@dataclass(frozen=True)
class SecretFinding:
    """One credential detected in a document."""

    path: str
    line: int
    kind: str
    prefix: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind} ({self.prefix}...)"


# Private key blocks: the PEM header (RSA, EC, OPENSSH, ENCRYPTED, none),
# and PGP's, which ends `PRIVATE KEY BLOCK-----`. The header is matched on
# its own, so prose quoting one is a finding too: the scan reads a line at
# a time and can't see whether a key body follows.
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY(?: BLOCK)?-----")

# AWS access key IDs: 20 chars, AKIA for a long-term key and ASIA for a
# temporary (STS) one. Bounded by "not alphanumeric" rather than `\b`, which
# doesn't fire between `_` and `A`, so `KEY_AKIA...` was missed.
_AWS_KEY_RE = re.compile(r"(?<![0-9A-Za-z])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])")

# GitHub tokens: ghp_ (personal), gho_ (OAuth), ghs_ (server-to-server),
# ghu_ (user-to-server), ghr_ (refresh), and fine-grained github_pat_.
_GITHUB_RE = re.compile(
    r"\b(?:(?:ghp|gho|ghs|ghu|ghr)_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})\b"
)

# Slack tokens: xox[abprs]- followed by token segments
_SLACK_RE = re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{9,}[0-9A-Za-z]\b")

# Anthropic API key prefix
_ANTHROPIC_RE = re.compile(r"\bsk-ant-[0-9A-Za-z_-]{20,}\b")

# OpenAI API keys: a project, admin or service-account key (`sk-proj-` and
# so on, then base64url), or a legacy key (`sk-` then 32 or more letters and
# digits, no hyphen). A bare `sk-` followed by any 20 word characters read
# "sk-learn-compatible-pipeline-for-evaluations" as a key.
_OPENAI_RE = re.compile(r"\bsk-(?:(?:proj|admin|svcacct)-[0-9A-Za-z_-]{20,}|[0-9A-Za-z]{32,})\b")

# Google API key: AIza followed by 35 characters
_GOOGLE_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")

# JWT candidate: 3 dot-separated base64url segments. The first is bounded:
# `\b` fires after every `-`, so with `+` each hyphen in a long run started a
# match that consumed the whole run and backtracked, quadratic in the line
# (80k characters of "abc-abc-..." took 7 s; 9b, PR 403). A JWT's first
# segment is its header, a small JSON object, so 1024 loses no real token.
_JWT_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9_-]{1,1024}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def _is_jwt(token: str) -> bool:
    """True if token is a 3-segment JWT with a decodable header that names `alg`."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    header_b64, payload_b64, sig_b64 = parts
    if not header_b64 or not payload_b64 or not sig_b64:
        return False
    padding = "=" * (-len(header_b64) % 4)
    try:
        header_bytes = base64.urlsafe_b64decode((header_b64 + padding).encode("ascii"))
        header_json = json.loads(header_bytes.decode("utf-8"))
        if isinstance(header_json, dict) and "alg" in header_json:
            return True
    except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return False


def scan_line(line: str) -> list[tuple[str, str]]:
    """Scan a single line for secrets, returning list of (kind, matched_secret)."""
    findings: list[tuple[str, str]] = []
    matched_spans: list[tuple[int, int]] = []

    def add_match(kind: str, start: int, end: int, val: str) -> None:
        if any(s < end and start < e for s, e in matched_spans):
            return
        if val in ALLOWED_EXAMPLE_SECRETS:
            return
        matched_spans.append((start, end))
        findings.append((kind, val))

    # 1. Private key blocks
    for m in _PRIVATE_KEY_RE.finditer(line):
        add_match("private key block", m.start(), m.end(), m.group(0))

    # 2. Anthropic API keys (checked before OpenAI since sk-ant- starts with sk-)
    for m in _ANTHROPIC_RE.finditer(line):
        add_match("Anthropic API key", m.start(), m.end(), m.group(0))

    # 3. OpenAI API keys
    for m in _OPENAI_RE.finditer(line):
        add_match("OpenAI API key", m.start(), m.end(), m.group(0))

    # 4. GitHub tokens
    for m in _GITHUB_RE.finditer(line):
        add_match("GitHub token", m.start(), m.end(), m.group(0))

    # 5. Slack tokens
    for m in _SLACK_RE.finditer(line):
        add_match("Slack token", m.start(), m.end(), m.group(0))

    # 6. Google API keys
    for m in _GOOGLE_KEY_RE.finditer(line):
        add_match("Google API key", m.start(), m.end(), m.group(0))

    # 7. AWS access key IDs
    for m in _AWS_KEY_RE.finditer(line):
        add_match("AWS access key ID", m.start(), m.end(), m.group(0))

    # 8. JWTs
    for m in _JWT_CANDIDATE_RE.finditer(line):
        token = m.group(0)
        if _is_jwt(token):
            add_match("JWT", m.start(), m.end(), token)

    return findings


def scan_text(text: str, filename: str = "") -> list[SecretFinding]:
    """Scan text line-by-line for credentials."""
    findings: list[SecretFinding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, matched_secret in scan_line(line):
            prefix = matched_secret[:4]
            findings.append(
                SecretFinding(
                    path=filename,
                    line=line_no,
                    kind=kind,
                    prefix=prefix,
                )
            )
    return findings


def scan_document(doc: ContentDocument) -> list[SecretFinding]:
    """Scan a ContentDocument for secrets."""
    if doc.path.is_file():
        text = doc.path.read_text(encoding="utf-8")
    else:
        from policyforge.content.tree import render_document

        text = render_document(doc.metadata, doc.body)
    return scan_text(text, filename=doc.relative_path)

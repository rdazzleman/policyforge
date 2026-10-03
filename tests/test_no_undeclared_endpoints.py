"""C-01 — no endpoint this project contacts is undeclared.

`docs/commitments.md` states that PolicyForge contacts nothing the operator
did not configure: no telemetry, no analytics, no licence check, no update
ping. That is the commitment an adopter is least able to verify for
themselves and most wants verified — a compliance tool that quietly reported
usage would be a finding in its own generated Standards.

Prose is the wrong form for it. The claim is true today because nobody has
added a `requests.post` to a metrics endpoint, and it stays true only while
somebody keeps not doing that. A reviewer reading a diff sees a new URL
constant and has no way to know whether it was always there.

So the allowlist below is the declaration, and this test is the enforcement:
every host-shaped literal in `src/` is either in it, with a stated reason, or
the test fails and names the file. Adding an endpoint is then a deliberate
act that edits this file, which is exactly the review conversation that
should happen.

Read over the parsed AST rather than by grepping the text, so a module stays
free to *discuss* an endpoint in its docstring — which several do, since
explaining where public catalog content comes from is part of their job.

What this does not prove: that a dependency contacts nothing. That is what
the hashed lockfile, pip-audit and the seven-day Dependabot cooldown are
for, and it is a different claim with different evidence.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import urlparse

import policyforge

SRC = Path(policyforge.__file__).resolve().parent

#: Every host this project may name, and why it may name it. A host outside
#: this mapping is a new outbound relationship, and the point of the test is
#: that adding one cannot be done quietly.
ALLOWED_HOSTS: dict[str, str] = {
    # Public catalog sources. Every one is fetched only by an explicit
    # `etl-*` command the operator runs, and every one serves government
    # works — the content this project is allowed to redistribute.
    "csrc.nist.gov": "NIST CPRT: the HIPAA-to-800-53 crosswalk (etl-hipaa-crosswalk)",
    "www.ecfr.gov": (
        "eCFR: the HIPAA Security Rule, 45 CFR 164 Subpart C (etl-hipaa), and "
        "information blocking, 45 CFR 171 (etl-info-blocking)"
    ),
    "www.cms.gov": "CMS: the ARC-AMPE Volume II control baseline (etl-arc-ampe)",
    "airc.nist.gov": (
        "NIST AIRC: the AI Risk Management Framework 1.0 Core "
        "(etl-ai-rmf). CPRT does not publish the Core, which is why this "
        "is a second NIST host rather than another csrc.nist.gov path"
    ),
    "raw.githubusercontent.com": (
        "NIST's OSCAL edition of SP 800-53 (etl-oscal) and FedRAMP's consolidated "
        "rules dataset (etl-fedramp)"
    ),
    "generativelanguage.googleapis.com": (
        "Google AI Studio's Gemini API, when `provider: gemini` is configured; "
        "receives the prompts that provider sends"
    ),
    "github.com": (
        "the GitHub wiki remote (export/github_wiki.py), cloned and pushed to only when a "
        "`github_wiki` target is configured — it RECEIVES the organization's documents, so "
        "it is a subprocessor and appears in docs/subprocessors.md; also cited as the "
        "provenance of OSCAL content in SSP output"
    ),
    "api.github.com": (
        "asked whether the wiki's repository is public (export/_wiki_auth.py), only when a "
        "`github_wiki` target is configured. One GET of the repository's metadata; no "
        "document content is sent"
    ),
    # Local model defaults. These are the reason `boundary.py` classifies a
    # loopback endpoint as `local`: nothing leaves the host.
    "localhost": "default base_url for a model running on this machine",
    "127.0.0.1": "same, for llama-server",
    # Placeholders in help text and docstrings. The real Confluence host is
    # always supplied by the operator on the command line or in config —
    # there is no default, deliberately, because a default would be somebody
    # else's wiki.
    "yourorg.atlassian.net": "placeholder in CLI help; the real host is operator-supplied",
    "x.atlassian.net": "same, shortened for line length",
}

#: Matches a URL in a string constant. Deliberately loose on the scheme and
#: strict about needing one: a bare word with a dot in it is a filename far
#: more often than a host, and reporting `schema.json` would make this noisy
#: enough to switch off.
_URL = re.compile(r"\b(?:https?|ws|wss|ftp)://[^\s'\"<>)\]}]+", re.IGNORECASE)


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _hosts_in(path: Path) -> set[tuple[str, int]]:
    """Every (host, line) named by a string constant in this module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for match in _URL.findall(node.value):
            host = urlparse(match).hostname
            if host:
                found.add((host, getattr(node, "lineno", 0)))
    return found


def test_every_endpoint_in_source_is_declared():
    """A host-shaped literal anywhere in src/ is in ALLOWED_HOSTS."""
    undeclared: list[str] = []
    for path in _python_files():
        for host, line in sorted(_hosts_in(path)):
            if host not in ALLOWED_HOSTS:
                undeclared.append(f"{path.relative_to(SRC.parent)}:{line} -> {host}")

    assert not undeclared, (
        "Undeclared outbound endpoint(s). PolicyForge commits to contacting "
        "nothing the operator did not configure (docs/commitments.md, C-01). "
        "If this is a legitimate new endpoint, add it to ALLOWED_HOSTS with a "
        "reason and say so in the commitments doc:\n  " + "\n  ".join(undeclared)
    )


def test_no_telemetry_shaped_hostnames():
    """No allowlisted host looks like analytics, and the allowlist is honest.

    A second, blunter check on the allowlist itself, so that permitting a
    host is not enough to get telemetry in — somebody would also have to
    name it something that does not read as telemetry, which is a different
    kind of act than forgetting.
    """
    suspicious = ("telemetry", "analytics", "metrics", "tracking", "sentry", "segment")
    for host in ALLOWED_HOSTS:
        assert not any(word in host.lower() for word in suspicious), (
            f"{host} is allowlisted and reads as a telemetry endpoint"
        )


def test_allowlist_has_no_dead_entries():
    """Every allowlisted host is actually used.

    A stale allowlist is how this check quietly stops meaning anything: the
    entries accumulate, nobody removes them, and eventually the list permits
    more than the code does. Better for the removal of an endpoint to also
    require removing its permission.
    """
    seen = {host for path in _python_files() for host, _ in _hosts_in(path)}
    dead = sorted(set(ALLOWED_HOSTS) - seen)
    assert not dead, (
        "ALLOWED_HOSTS names host(s) that no longer appear in src/. Remove "
        f"them so the list keeps matching the code: {', '.join(dead)}"
    )

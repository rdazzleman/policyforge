"""semgrep's version is stated in three places, and they must agree (#83).

1. `SEMGREP_IMAGE` in `scripts/semgrep_gate.py`: the image the local gate
   runs, as a version tag and the index digest Docker holds it to.
2. The image of ci.yml's `semgrep` job: what CI runs.
3. The `rev:` of the semgrep hook in `.pre-commit-config.yaml`.

This test began as a check that a requirements input and the hook agreed.
80 caught them disagreeing while reviewing the 1.177.0 bump, under a comment
that said they were the same. **A written intent fires only if somebody
recalls it; an assertion fires whether or not anyone remembers it exists.**
The lock is gone (#83) and the image replaces it, so the image is what the
others are held to now.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import semgrep_gate  # noqa: E402

#: `semgrep/semgrep:<version>@sha256:<64 hex>`: a tag for people, a digest
#: for Docker, which refuses any other content for it.
_IMAGE_RE = re.compile(r"^semgrep/semgrep:([0-9]+(?:\.[0-9]+)+)@sha256:([0-9a-f]{64})$")
#: The `rev:` line of the semgrep hook, which is `v`-prefixed.
_REV_RE = re.compile(
    r"repo:\s*https://github\.com/semgrep/semgrep\s*\n\s*rev:\s*v([0-9][^\s]*)",
)


def _ci_semgrep_image() -> str:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    container = workflow["jobs"]["semgrep"]["container"]
    return container["image"] if isinstance(container, dict) else container


def test_the_gates_image_is_pinned_by_tag_and_digest():
    """A tag alone can move under the same name. The digest can't."""
    assert _IMAGE_RE.fullmatch(semgrep_gate.SEMGREP_IMAGE), semgrep_gate.SEMGREP_IMAGE


@pytest.mark.github_config
def test_ci_runs_exactly_the_gates_image():
    """Character for character: the same tag AND the same digest, so the
    gate and CI can't run two semgreps."""
    assert _ci_semgrep_image() == semgrep_gate.SEMGREP_IMAGE


def test_the_pre_commit_hook_runs_the_images_version():
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hooked = _REV_RE.search(hooks)
    pinned = _IMAGE_RE.fullmatch(semgrep_gate.SEMGREP_IMAGE)

    # Absent and disagreeing are different answers, and a regex that quietly
    # matched nothing would make this pass forever.
    assert hooked, ".pre-commit-config.yaml no longer pins a semgrep rev"
    assert pinned, "SEMGREP_IMAGE isn't a tag and a digest"
    assert pinned.group(1) == hooked.group(1), (
        f"SEMGREP_IMAGE runs {pinned.group(1)} and the pre-commit hook runs "
        f"v{hooked.group(1)}. Move them together (CONTRIBUTING.md says how)."
    )


@pytest.mark.github_config
def test_nothing_still_names_semgreps_retired_lock():
    """The lock and its advisory audit went with #83. A file that still
    installs from it would fail where nothing tests it (the dev container
    did, until this PR)."""
    tracked = (
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".devcontainer" / "devcontainer.json",
        ROOT / ".github" / "dependabot.yml",
        ROOT / "scripts" / "check.py",
        ROOT / "scripts" / "ci_in_docker.py",
        ROOT / "scripts" / "lock_drift.py",
        ROOT / "CONTRIBUTING.md",
    )
    named = [p.name for p in tracked if "requirements/semgrep" in p.read_text(encoding="utf-8")]
    assert not named, named
    assert not (ROOT / "requirements" / "semgrep").exists()

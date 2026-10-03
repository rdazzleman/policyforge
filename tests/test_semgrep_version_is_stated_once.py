"""`requirements/semgrep/semgrep.in` claims it pins the same version as the
pre-commit hook. Nothing checked that, and a bump made it false.

**The comment is the whole of the invariant and it went quietly wrong.**
80 caught it reviewing the 1.177.0 bump: `semgrep.in` said `1.177.0`, the
hook still said `v1.176.1`, and the sentence *"Same version as the
pre-commit hook"* sat above both. Nothing failed — the hook runs one
version, the lock installs another — which is why it would have survived.

A written intent fires only if somebody recalls it; an assertion fires
whether or not anyone remembers it exists. That is this project's own
argument for `release_check.py`, pointed at a comment.

Two files rather than one because they are consumed by different things:
the hook runs from GitHub, the lock installs into `.tools/semgrep`. The
intent that they agree is real, so it gets a check rather than a deletion.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: `semgrep==1.177.0` in the requirements input.
_PIN_RE = re.compile(r"^semgrep==([0-9][^\s;]*)", re.MULTILINE)
#: The `rev:` line of the semgrep hook, which is `v`-prefixed.
_REV_RE = re.compile(
    r"repo:\s*https://github\.com/semgrep/semgrep\s*\n\s*rev:\s*v([0-9][^\s]*)",
)


def test_the_lock_and_the_pre_commit_hook_pin_one_version():
    requirements = (ROOT / "requirements" / "semgrep" / "semgrep.in").read_text(encoding="utf-8")
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    pinned = _PIN_RE.search(requirements)
    hooked = _REV_RE.search(hooks)

    # Absent and disagreeing are different answers, and a regex that quietly
    # matched nothing would make this pass forever.
    assert pinned, "requirements/semgrep/semgrep.in no longer pins semgrep=="
    assert hooked, ".pre-commit-config.yaml no longer pins a semgrep rev"

    assert pinned.group(1) == hooked.group(1), (
        f"semgrep.in pins {pinned.group(1)} and the pre-commit hook runs "
        f"v{hooked.group(1)}. semgrep.in says they are the same version. "
        f"Bump both, or change that comment to say they are deliberately "
        f"independent — but do not leave the sentence asserting something "
        f"untrue, because the next reader will believe it."
    )

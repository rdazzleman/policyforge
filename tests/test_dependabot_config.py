""".github/dependabot.yml: one pip entry per directory, grouped per dependency (#414).

Two pip entries reached the same locks, so every bump arrived as two pull
requests. GitHub forbids overlapping directories across blocks of one
ecosystem. These pin the shape that fixes it; whether Dependabot then opens
one pull request per bump can only be seen on a live run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / ".github" / "dependabot.yml"


def _updates() -> list[dict]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["updates"]


def _dirs(update: dict) -> list[str]:
    return [update["directory"]] if "directory" in update else list(update["directories"])


def test_no_directory_appears_in_two_blocks_of_one_ecosystem():
    """GitHub's literal rule: one directory, one block per ecosystem.

    **This does NOT catch #414's shape**, and says so: "/" and
    "/requirements" are different strings, yet "/" also reached the locks
    under requirements/. Splitting the pip entry back into those two blocks
    passes this test (measured on #414); the test below is the #414 guard.
    """
    seen: dict[tuple[str, str], int] = {}
    for index, update in enumerate(_updates()):
        for directory in _dirs(update):
            key = (update["package-ecosystem"], directory.rstrip("/") or "/")
            assert key not in seen, (
                f"{key} is in update blocks {seen[key]} and {index}: GitHub forbids "
                "one directory in two blocks of one ecosystem"
            )
            seen[key] = index


def test_the_lock_directories_are_one_block_grouped_per_dependency():
    """Both "/" (pyproject) and "/requirements" (the two locks) reach the same
    files, so they are one block, and `group-by: dependency-name` makes one
    pull request per bump across them."""
    blocks = [u for u in _updates() if u["package-ecosystem"] == "pip" and "/" in _dirs(u)]
    assert len(blocks) == 1, blocks
    (block,) = blocks
    assert set(_dirs(block)) == {"/", "/requirements"}
    groups = block.get("groups") or {}
    # The per-dependency group must be the ONLY group, and cover every
    # dependency, every version update (policyforge-9b on #454). Each of
    # these went back to one pull request per directory -- #414's defect --
    # with a weaker version of this test green: `patterns` narrowed to
    # `["pydantic*"]`; `applies-to: security-updates`, which drops version
    # bumps out of the group; a narrower group listed first, which wins on
    # Dependabot's first-match order. So this is a WHITELIST of what the
    # group may say, not a list of the keys known to narrow it: a key added
    # tomorrow fails here until someone decides it is harmless.
    assert len(groups) == 1, f"the per-dependency group must be the only group: {sorted(groups)}"
    ((name, group),) = groups.items()
    assert set(group) <= {"patterns", "group-by", "applies-to"}, (
        f"group {name!r} carries {sorted(set(group) - {'patterns', 'group-by', 'applies-to'})}, "
        "which can narrow it"
    )
    assert group.get("group-by") == "dependency-name", group
    assert group.get("patterns") == ["*"], group
    assert group.get("applies-to", "version-updates") == "version-updates", group

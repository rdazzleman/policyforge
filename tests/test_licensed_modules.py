"""Every check reads the ONE list of licensed modules (#48).

Four hand-kept copies once disagreed: three named five modules while #32
part 3 moved seven. The cross-check in test_licensed_parsers_absent.py holds
the list against part 3's diff; this holds every consumer to the list, so a
local copy added back to one of them fails here instead of drifting quietly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import licensed_modules  # noqa: E402


@pytest.mark.parametrize("consumer", ["publish_guard", "release_check", "release"])
def test_each_guard_reads_the_one_list_not_a_copy(consumer):
    module = __import__(consumer)
    assert module.LICENSED_MODULES is licensed_modules.LICENSED_MODULES, consumer
    assert module.LICENSED_PATHS is licensed_modules.LICENSED_PATHS, consumer


def test_the_public_assembler_test_reads_the_one_list():
    import test_levelled

    assert test_levelled.PRIVATE is licensed_modules.LICENSED_MODULES

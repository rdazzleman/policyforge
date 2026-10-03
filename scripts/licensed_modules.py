"""The licensed-framework modules that leave the public tool: ONE list (#48).

The user's decision (2026-09-28, #32): the parsers that read a licensed
vendor's file are not part of the public, Apache-licensed PolicyForge. #32
part 3 (#50) moved seven modules to the private repository. Every check that
must know which modules those are reads them HERE:

    scripts/publish_guard.py   L1 paths, L2 historical blobs, L3 imports and names
    scripts/release_check.py   the released archive must carry none of them
    scripts/release.py         (through release_check) what the public commit drops,
                               and the classifier's candidates (#57's _licensed_file)
    tests/test_levelled.py     the public assembler imports none of them

Before this, four hand-kept copies existed, and three of them named five
modules while part 3 moved seven, so `byoc_loader` and `licensed_builtin`
were known to no guard by name (ba, #48).

NEVER read from the tree being checked: a candidate could edit its own copy
of this list, the same self-exemption the guard refuses for defined names.
It is read from the tool's own checkout. `tests/test_licensed_parsers_absent.py`
keeps its own hand-written MOVED, taken from part 3's diff, and asserts it
equals this, so the two sides of that check stay two instruments.

Moving another module to the private repository means adding it here; the
absent-test's cross-check fails until MOVED says the same.
"""

from __future__ import annotations

LICENSED_MODULES = (
    "hitrust",
    "hitrust_export",
    "govramp",
    "govramp_export",
    "soc2_tsc",
    "byoc_loader",  # the loader both export parsers share
    "licensed_builtin",  # #36's in-tree stand-in, now the private package's plugin.py
)
MODULE_DIR = "src/policyforge/ingest"
LICENSED_PATHS = tuple(f"{MODULE_DIR}/{name}.py" for name in LICENSED_MODULES)

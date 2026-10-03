"""Licensed module names, put back into fixture text at run time (#123).

The publish guard (`scripts/publish_guard.py`, layer L3) refuses a public
tree whose text spells an import of a licensed module, in code or in a
string: a monkeypatch target or a generated-code template ships the
dependency as surely as an `import` does. The tests of that guard, and of the
gates beside it, must write exactly such text into their temporary
repositories to prove the gates refuse it. Spelled literally, those fixtures
made the first public release, a root commit scanned whole, fail its own
guard (9b, #123).

So these tests spell each licensed module as a placeholder, `@HITRUST@`, and
`unmask` puts the name back from `LICENSED_MODULES`, the one list (#48). The
value a test uses is byte for byte what it was; only the file's text changes.
80 ruled this on #123 over teaching the guard to tell a fixture from an
import, which would have relaxed its string rule.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from licensed_modules import LICENSED_MODULES

_PLACEHOLDER = re.compile(r"@([A-Z0-9_]+)@")


def unmask(text: str) -> str:
    """`text` with every `@MODULE@` replaced by that licensed module's name.

    A placeholder that names no licensed module is an error, not left in
    place: a typo would otherwise turn a must-fail fixture into text the gate
    under test has no reason to refuse, and the test would pass for nothing.
    """

    def name(match: re.Match[str]) -> str:
        module = match.group(1).lower()
        if module not in LICENSED_MODULES:
            raise KeyError(f"{match.group(0)} names no licensed module ({LICENSED_MODULES})")
        return module

    return _PLACEHOLDER.sub(name, text)

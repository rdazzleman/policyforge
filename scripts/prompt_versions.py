"""Record each registered prompt's current version in the version ledger (#236).

    python scripts/prompt_versions.py           list what would be added; exit 1
                                                if the ledger is behind or wrong
    python scripts/prompt_versions.py --write   add the new versions

**A version keeps one text for life.** This adds a version the ledger does
not have yet, with the commit it was recorded at, and never rewrites one it
has: a registered prompt whose version is already recorded with a different
fingerprint is refused, and the fix is a new version number in the code,
not an edit here. `evals/prompt-versions.json` is read by
`tests/test_prompt_fingerprint_file.py` and by the eval runner's report.

This script cannot stop a hand edit of the ledger: rewriting a recorded
fingerprint makes both it and the test green (1d on #422). That the ledger
only grows is held by review of its diff.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "evals" / "prompt-versions.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help="add the new versions")
    args = parser.parse_args(argv)

    from policyforge.llm import prompts

    prompts.load_all()
    record = json.loads(LEDGER.read_text(encoding="utf-8"))
    ledger = record["prompts"]
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()

    added, refused = [], []
    for name, prompt in sorted(prompts.REGISTRY.items()):
        entries = ledger.setdefault(name, {}).get(str(prompt.version))
        if entries is None:
            ledger[name][str(prompt.version)] = [
                {"fingerprint": prompt.fingerprint, "first_seen": head}
            ]
            added.append(f"{name} v{prompt.version} {prompt.fingerprint}")
        elif [e["fingerprint"] for e in entries] != [prompt.fingerprint]:
            refused.append(
                f"{name} v{prompt.version}: recorded {[e['fingerprint'] for e in entries]}, "
                f"now {prompt.fingerprint} -- give the changed text a new version"
            )
    for line in added:
        print(f"+ {line}")
    for line in refused:
        print(f"REFUSED {line}", file=sys.stderr)
    if refused:
        return 1
    if args.write and added:
        LEDGER.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {len(added)} version(s) to {LEDGER.relative_to(ROOT)}")
        return 0
    return 1 if added else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Convenience wrapper so you can run the NIST vault ETL without installing
the package first: `python scripts/vault_to_data_etl.py <controls_dir>`.

Does the same thing as `policyforge etl-vault --controls-dir <controls_dir>`
once the package is pip-installed — use whichever is more convenient.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from policyforge.cli import etl_vault

if __name__ == "__main__":
    etl_vault()

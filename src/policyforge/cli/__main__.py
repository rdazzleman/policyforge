"""`python -m policyforge.cli`.

The single-file module ran its commands from an `if __name__ == "__main__"`
guard. A package runs this file instead, so without it that invocation would
have stopped working at the split with nothing else failing.
"""

from policyforge.cli import cli

cli()

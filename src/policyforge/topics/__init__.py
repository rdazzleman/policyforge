"""Topic registry and ownership/coverage analysis.

`registry.py` declares which topics exist and who owns each; `coverage.py`
reports which in-scope controls are owned by nobody (orphaned) or by more
than one team (contested). Every topic has a single accountable team, so a
control owned by none, or by two, is the gap these reports exist to show.
"""

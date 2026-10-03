"""PolicyForge as tools another agent can call.

See `server.py`. Deliberately a package of its own rather than a module
inside `zardoz/`: the import guard that keeps the publish path unreachable
is written per-package, and this surface needs its own.
"""

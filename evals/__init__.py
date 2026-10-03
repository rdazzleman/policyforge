"""Graded cases for the prompts, run against a real model.

Separate from `tests/` on purpose: these cost money and need network, and a
suite people cannot run offline is a suite people stop running. The grading
logic is importable and unit-tested without an API key, because a harness
whose own scoring is wrong is worse than no harness.
"""

"""LLM-driven editing of published Confluence pages.

`plan.py` turns an instruction into a reviewable list of edits; `apply.py`
carries an approved plan out and checks the result for damage nobody asked
for. The CLI's `edit-confluence` command wires them to the Confluence
fetch/publish path and is where the safety gates live — see README's
"Editing a live page".
"""

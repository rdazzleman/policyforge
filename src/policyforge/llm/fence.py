"""A delimiter that the text it wraps cannot contain.

Two paths in this project put text somebody else wrote into the same request
as the rules governing what to do with it: Zardoz answers from retrieved
passages, and the Confluence editor rewrites a live wiki page. Both need the
same thing — a marker saying "quoted material starts here and stops there"
that the quoted material itself has no way to forge.

A fixed delimiter cannot do that. A `---` rule or a `### DOCUMENT` heading is
something any page can simply write, and a page that writes the closing
marker puts everything after it back on the instruction side of the fence.

Choosing the token *after* the text is known removes the problem: the token
is checked against the text it will wrap, so a document written yesterday
cannot contain a value generated a moment ago. The collision loop costs
nothing and is there because "astronomically unlikely" is not the same as
"impossible", and this is the one place where the difference would be silent.
"""

from __future__ import annotations

import secrets


def fence_token(*texts: str) -> str:
    """A delimiter for this one request that none of `texts` contains."""
    haystack = "\n".join(texts)
    while True:
        token = f"pf-{secrets.token_hex(8)}"
        if token not in haystack:
            return token

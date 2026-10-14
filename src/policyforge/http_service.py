"""Which service an HTTP failure came from (#413).

`cli._Group` turns a failed HTTP request into one line with what to check. The
advice for a refused credential is different for each service, and a status
code can't say which one answered: a 401 from the model provider got the
Confluence advice (check the space and the token), sending the user to fix the
wrong credential (9b, on PR 412). So the code that MADE the request says what
it was talking to, by tagging the exception on its way out, and the one line is
built from that tag. An exception nobody tagged is described without a guess.

Two sites tag today: the provider wrapper (`llm/ledger.py`, for the model
provider and the variable `llm.api_key_env` names) and the Confluence
functions (`export/_confluence_auth.reports_as_confluence`, for Confluence and
the token variable the call was given).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

#: The attribute the tag is kept under, on the exception object.
SERVICE_ATTR = "policyforge_service"

LLM = "llm"
CONFLUENCE = "confluence"


@dataclass(frozen=True)
class Service:
    """What a request was made to: its kind, how to say it, and the name of
    the environment variable or config key that holds its credential."""

    kind: str
    name: str
    credential: str = ""


def tag(exc: BaseException, service: Service) -> None:
    """Record `service` on `exc`, unless something nearer the request already
    did: the first tagger is the one that made the request."""
    if getattr(exc, SERVICE_ATTR, None) is None:
        with contextlib.suppress(AttributeError, TypeError):  # one that takes no attributes
            setattr(exc, SERVICE_ATTR, service)


def service_of(exc: BaseException) -> Service | None:
    found = getattr(exc, SERVICE_ATTR, None)
    return found if isinstance(found, Service) else None

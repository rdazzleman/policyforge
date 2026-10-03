"""Whether a cited passage actually supports the claim made from it."""

from .base import (
    CONTRADICTED,
    ENTAILED,
    NEUTRAL,
    Conflict,
    Entailer,
    Unsupported,
    Verdict,
    conflicting_passages,
    get_entailer,
    unsupported_claims,
)

__all__ = [
    "CONTRADICTED",
    "ENTAILED",
    "NEUTRAL",
    "Conflict",
    "Entailer",
    "Unsupported",
    "Verdict",
    "conflicting_passages",
    "get_entailer",
    "unsupported_claims",
]

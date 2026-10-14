"""The programme's scope facts: what the organisation is, as far as the topic
set goes (22, part 3; 80's 5925 and 5927).

Five facts, declared under `programme:` in `config/config.yaml`:

    programme:
      builds_software: true     # turns Secure Development on
      own_facilities: false     # turns Physical & Environmental on
      personal_data: phi        # none | pii | phi: splits Privacy out
      baseline: 800-53          # 800-53 | fedramp | govramp: keeps Personnel
                                # Security and System Maintenance
      ai: none                  # none | user | builder: sizes the AI block

**Unset is not `none`.** A fact nobody declared is `None` here, and a reader
that must decide (the topic rule, the Acceptable Use Policy's AI section)
says which it did. An organisation that has not said whether it builds
software is not one that has said it doesn't.

A value outside the vocabulary refuses, naming the key and the allowed
values, rather than being read as off: a misspelt `phi` that silently meant
`none` would leave an organisation's PHI topics out of its registry.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

PERSONAL_DATA = ("none", "pii", "phi")
BASELINES = ("none", "800-53", "fedramp", "govramp")
AI_ROLES = ("none", "user", "builder")

#: The facts that are yes or no.
FLAGS = ("builds_software", "own_facilities")
#: The facts with a vocabulary, and what it is.
CHOICES = {"personal_data": PERSONAL_DATA, "baseline": BASELINES, "ai": AI_ROLES}


class ScopeFactError(ValueError):
    """A scope fact that isn't one this version knows how to read."""


@dataclass(frozen=True)
class ScopeFacts:
    """What the programme declared. `None` is "not declared", never "no"."""

    builds_software: bool | None = None
    own_facilities: bool | None = None
    personal_data: str | None = None
    baseline: str | None = None
    ai: str | None = None

    def declared(self) -> dict[str, object]:
        """Each fact that was declared, by name."""
        return {
            f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None
        }

    def undeclared(self) -> list[str]:
        """The facts nobody declared, in the order above."""
        return [f.name for f in fields(self) if getattr(self, f.name) is None]

    def overlaid_with(self, other: ScopeFacts) -> ScopeFacts:
        """These facts, with every fact `other` declares taking its place."""
        return replace(self, **other.declared())

    # The reads the topic rule makes: undeclared answers "no", and says so
    # through `undeclared()`, not by pretending to have been told.

    @property
    def handles_personal_data(self) -> bool:
        return self.personal_data in ("pii", "phi")

    @property
    def handles_phi(self) -> bool:
        return self.personal_data == "phi"

    @property
    def has_assessed_baseline(self) -> bool:
        return self.baseline in ("800-53", "fedramp", "govramp")


def parse_scope_facts(programme: object) -> ScopeFacts:
    """The facts in a `programme:` block (a mapping, or None).

    Keys other than the five are left alone: `catalogs:` lives there too.
    """
    if programme is None:
        return ScopeFacts()
    if not isinstance(programme, dict):
        raise ScopeFactError("`programme:` in config must be a mapping.")
    values: dict[str, object] = {}
    for name in FLAGS:
        if name not in programme or programme[name] is None:
            continue
        value = programme[name]
        if not isinstance(value, bool):
            raise ScopeFactError(
                f"`programme.{name}` must be true or false (YAML booleans, unquoted); "
                f"it is {value!r}."
            )
        values[name] = value
    for name, allowed in CHOICES.items():
        if name not in programme or programme[name] is None:
            continue
        value = programme[name]
        # `none` written bare is YAML null, handled above as "not declared";
        # `"none"` and the other words arrive as strings.
        if not isinstance(value, str) or value.strip().lower() not in allowed:
            raise ScopeFactError(
                f"`programme.{name}` must be one of {', '.join(allowed)}; it is {value!r}."
            )
        values[name] = value.strip().lower()
    return ScopeFacts(**values)


def load_scope_facts(config: dict | None) -> ScopeFacts:
    """The facts in a loaded config dict."""
    return parse_scope_facts((config or {}).get("programme"))

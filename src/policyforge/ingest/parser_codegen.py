"""LLM-assisted codegen for BYOC (licensed) framework loaders.

HITRUST/GovRAMP exports don't have a stable, publicly documented column
layout this project can hand-write a parser against ahead of time -- every
org's MyCSF/GovRAMP export can differ, and a MyCSF CSV names its columns
`Textbox52` and `Textbox105` besides. The licensed-framework plugin, where
installed, handles the renderings that have actually been seen, by
recognising caption columns and value shapes. This module is what happens
without it, or when that fails: it sends a
sample export to your configured LLM provider and asks it to write a
*deterministic* Python parser for that specific file.

**What the model is asked for changed once HITRUST was understood.** The
first version asked for a parser that returned finished `Control` objects,
which meant every generated loader re-implemented the same four things --
deduplicating the report's repeated rows, separating maturity levels from
regulatory overlays, splitting `NIST SP 800-53 r5 PL-11` into a source and
an identifier, and assembling requirements under a control reference. Those
are properties of the framework, not of the file, and a model reinventing
them per export got them subtly different every time.

So for HITRUST the model is now asked for the small part only: read the
file, fill in a `levelled.Record` per (control reference x level) row, and
return them. `levelled.build_controls` does the rest, deterministically, the
same way for every export shape. The generated code shrinks to the part
that genuinely varies, and the part that must not vary stops being
generated at all.

This is a one-time, human-in-the-loop codegen step, not a runtime
dependency: the `generate-parser` command (cli/etl.py) writes the result to disk
once, then you review, test, and commit it like any other source file.
Nothing under `ingest/*_loader.py` calls an LLM at parse time -- only this
module does, and only when you explicitly run that command.

WARNING: this sends the *entire content* of the sample export you point it
at to your configured LLM provider's API. If that sample is licensed/
contractual content (a real MyCSF or GovRAMP export), confirm your license
terms actually permit sending it to a third-party API processor before
running this.
Passing a *trimmed* sample -- a dozen rows with the header intact -- is
usually enough to derive a parser and sends far less.
"""

from __future__ import annotations

import re

from policyforge.ingest.parser_gate import ALLOWED_IMPORTS
from policyforge.llm.base import LLMProvider

# Kept in sync with schema.py by hand (not imported) so the prompt shows the
# model plain source rather than a runtime-introspected dump.
_CONTROL_SCHEMA = """\
@dataclass
class ControlEnhancement:
    enhancement_id: str
    title: str
    baseline: str
    description: str


@dataclass
class Control:
    control_id: str
    title: str
    framework: str          # e.g. "HITRUST-CSF", "GovRAMP"
    framework_version: str  # e.g. "v11.8"
    family: str | None = None
    family_abbr: str | None = None
    baseline: str | None = None
    control_statement: str = ""
    discussion: str = ""
    enhancements: list[ControlEnhancement] = field(default_factory=list)
    related_controls: list[str] = field(default_factory=list)
    source_crosswalk: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None
"""

_RECORD_SCHEMA = """\
@dataclass
class Record:
    category: str = ""               # "NN.0 - <category title>"
    objective: str = ""              # "NN.NN <objective title>"
    objective_statement: str = ""    # the Control Objective prose
    reference: str = ""              # "NN.a <control title>"
    specification: str = ""          # the Control Specification prose
    factor_type: str = ""            # "Organizational" or "System"
    level: str = ""                  # "Level 1", "Level FedRAMP" -- verbatim
    statement: str = ""              # the level's Implementation requirement
    organizational_factors: str = ""  # raw cell; newline-separated entries
    system_factors: str = ""          # raw cell
    regulatory_factors: str = ""      # raw cell
    mapping: str = ""                 # raw cell; one "<source> <id>" per line
"""

#: What a model needs to know about HITRUST to read an export of it, as
#: distinct from what it needs to know about the file. Mirrors
#: `ingest/levelled.py`'s module docstring; that file is the long form.
_HITRUST_BRIEFING = """\
HITRUST CSF facts that decide how an export is read:

* The hierarchy is four tiers deep:
      Control Category      "NN.0 - <category title>"
        Control Objective   "NN.NN <objective title>"
          Control Reference "NN.a <control title>"
            Requirement     one per (control reference x level)
  A table export repeats the upper three tiers on every row.

* One row is one requirement statement, keyed by (control reference,
  level). It is NOT one control: a control reference appears on as many
  rows as it has levels.

* "Level" spans two different things in one column. `Level 1`, `Level 2`
  and `Level 3` are an ordered maturity ladder. Everything else --
  `Level HIPAA`, `Level FedRAMP`, `Level CMS`, `Level FTI Custodians`,
  `Level GDPR`, and many more -- is a regulatory or segment overlay,
  switched on by a scoping factor rather than by rigour. Copy the label
  verbatim; do not parse, rank or normalize it.

* A MyCSF CSV export is a rendered SSRS report, so expect:
  - Column headers that are SSRS textbox names (`Textbox52`, `Textbox105`)
    and mean nothing. Identify columns by their contents, or by the caption
    column immediately to their left -- a column holding nothing but
    "Level 1 Organizational Factors:" describes the column after it.
  - Whole rows repeated verbatim, often many times over. Do NOT
    deduplicate; return every row and let `build_controls` collapse them.
  - Level names at most 36 characters long, so a long overlay name can end
    mid-word, and its full form may be nowhere in the file (not even in the
    caption columns). Copy the label as it is; do not complete it.
  - Columns that are entirely empty (Topics, and often Regulatory Factors).

* Factor and mapping cells hold a list, one entry per line, with CRLF line
  breaks inside quoted CSV cells and `<br/>` in an HTML rendering. Pass the
  cell through whole and unsplit -- `build_controls` splits it.

* Mapping lines look like `NIST SP 800-53 r5 PL-11`, `Example State Dept
  Title 10 Section 123.45 (d)(3)(x)` or `Example Accreditor (v2016) EA
  IM.01.02.03, EP 1` (the first is a public NIST id; the other two are
  invented to show the shapes real sources take). There is no delimiter
  between the authoritative source and its identifier and both halves
  contain spaces. Do not attempt to split them; `build_controls` learns the
  source vocabulary from the whole export and splits them correctly.
"""

_SYSTEM_PROMPT = """You are a Python codegen engine that writes ONE ETL \
loader function for a compliance-framework export, targeting an existing \
dataclass schema in a larger project.

Rules:
- Output ONLY a single, complete, well-formed Python module — no markdown
  code fences, no prose before or after the code.
- The module must define exactly one function,
  `load_<framework_slug>_export(export_path: Path) -> list[Control]`
  (substitute the literal framework slug given below), that parses the
  export format shown in the sample and returns a list of objects matching
  the target schema given below exactly (field names, types, defaults) —
  do not invent new fields.
- Parsing must be fully deterministic, and the module may import only
  these: ALLOWED_IMPORTS — plus the relative imports named below. Anything
  else, pandas included, is refused before the parser is ever run. Never
  call an LLM, the network, a subprocess, or any nondeterministic API.
- Start with `from __future__ import annotations`, then only the imports
  the function actually uses, then the relative imports named below — the
  generated module lives inside the same `ingest/` package.
- Never write to disk. The content being parsed is licensed, and the
  caller decides whether any of it is persisted: no `open(..., "w")`, no
  `write_text`, no `to_csv`, no caching.
- If a field isn't present in the sample, leave it at its dataclass
  default rather than inventing a value.
- Write one short module docstring (2-3 sentences) describing the export
  format this loader was derived from, so a future maintainer knows why
  the parsing logic looks the way it does. No other comments unless a
  parsing decision is genuinely non-obvious.
""".replace(
    # Read from the gate rather than written out, so the prompt can never
    # offer an import `check_generated_parser` would then refuse. It did
    # once: pandas was offered here and is not a dependency of this project.
    "ALLOWED_IMPORTS",
    ", ".join(sorted(ALLOWED_IMPORTS - {"__future__"})),
)

_HITRUST_RULES = """\
- Import `from .levelled import Record, build_controls`.
- Build one `Record` per row of the export and return
  `build_controls(records, source_path=str(export_path))`. Do NOT
  deduplicate rows, split mapping strings, classify levels, or assemble
  Control objects yourself — `build_controls` does all of that, and doing
  it twice produces a different catalog than every other loader.
- Set only the `Record` fields the export actually has. Pass multi-line
  cells (factors, mappings) through unsplit, exactly as read.
"""

_GENERIC_RULES = """\
- Import `from .schema import Control, ControlEnhancement`.
- Set `control.framework` and `control.framework_version` to sensible
  literal values for this framework (given below) rather than reading them
  from the export, unless the export itself contains that information.
- Always set `control.source_path = str(export_path)` on every Control you
  return.
"""


def target_schema(framework_slug: str) -> tuple[str, str]:
    """The schema source and the extra rules for one framework's parser.

    HITRUST has a normalization pipeline of its own that a generated parser
    should feed rather than duplicate; everything else builds Controls
    directly.
    """
    if framework_slug.lower().startswith("hitrust"):
        return _RECORD_SCHEMA, _HITRUST_RULES
    return _CONTROL_SCHEMA, _GENERIC_RULES


def briefing(framework_slug: str) -> str:
    """What the model should know about the framework itself, if anything."""
    return _HITRUST_BRIEFING if framework_slug.lower().startswith("hitrust") else ""


def _strip_code_fence(text: str) -> str:
    """Defensively strip a ```python ... ``` fence if the model wrapped its
    output in one despite being told not to."""
    match = re.match(r"^```(?:python)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    return match.group(1) if match else text


def generate_byoc_parser(
    *, framework: str, framework_slug: str, sample_text: str, provider: LLMProvider
) -> str:
    """Ask `provider` to write a deterministic parser for `sample_text`
    (the content of a BYOC sample export) targeting this project's schema.

    Returns raw Python source, unchecked. The sample is part of the prompt,
    so this is code written under the influence of an untrusted file: the
    caller runs `parser_gate.check_generated_parser` on it before writing it
    anywhere importable, and `parser_gate.trial_run` before trusting it."""
    if not sample_text.strip():
        raise ValueError("sample_text is empty — nothing to derive a parser from.")

    schema, rules = target_schema(framework_slug)
    framework_briefing = briefing(framework_slug)

    prompt = (
        f"Framework: {framework}\n"
        f"Framework slug (for the function name): {framework_slug}\n\n"
        f"{framework_briefing}\n"
        f"Framework-specific rules for this parser:\n{rules}\n"
        f"Target schema (from ingest/):\n\n{schema}\n\n"
        f"Sample export content:\n\n{sample_text}\n\n"
        "Write the loader module per the rules above."
    )
    from policyforge.llm import effort

    response = effort.call(
        provider,
        effort=effort.DRAFTING,
        system=_SYSTEM_PROMPT,
        prompt=prompt,
        temperature=0,
        max_tokens=8192,
    )
    return _strip_code_fence(response.text.strip()) + "\n"

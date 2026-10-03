# NIST AI Risk Management Framework 1.0

The Core, as a control catalog: **19 categories carrying 72 subcategories**,
across the four functions NIST defines.

| Function  | Categories | Subcategories |
| --------- | ---------- | ------------- |
| Govern    | 6          | 19            |
| Map       | 5          | 18            |
| Measure   | 4          | 22            |
| Manage    | 4          | 13            |
| **Total** | **19**     | **72**        |

Every number above is derived from `controls.json` by
`tests/test_ai_rmf_catalog.py`, which fails if this table and the catalog
disagree. Five of the six catalog READMEs here were audited in September
2026 and three carried claims that could not be derived from their own
`controls.json`; this table is checked so it cannot join them.

That check earned itself immediately. The first draft of this table said
Measure 21 and Manage 14 — **wrong in two cells and right in the total**,
because the figures were written to sum to a number already known to be
correct. A reconciling total is not evidence about its components.

A category is a `Control` and its subcategories are its enhancements —
the same shape 800-53 uses for a control and its enhancements.

## Read this before citing the catalog

**This catalog states outcomes. Every other catalog in this project states
obligations. That difference changes what a citation to it proves.**

800-53 says *the organization shall*. HIPAA says *a covered entity must*.
The AI RMF Core says things like:

> Legal and regulatory requirements involving AI are understood, managed,
> and documented.

That is a state of the world, not an instruction. Nobody is named, no act
is required, and there is no test an assessor could fail you against. NIST
publishes the *actions* separately, in the **Playbook**, which is
explicitly voluntary and versioned apart from the Framework.

Two consequences, both of which bite in practice.

### A citation here can be traceable and hollow at the same time

`satisfies` will resolve `[NIST AI RMF Govern 1.1]`, the tag is legal, and
the gate goes green. None of that establishes that the sentence carrying
the tag commits anyone to anything — it may simply restate the outcome in
new words.

**The difference that matters is that this is a property of the source
rather than of the parse, so it can only be disclosed, not fixed.** A
miscounted catalog is countable, plausible and wrong, and a better parser
closes it. This one is citable, resolvable and hollow, and no parser can
close it — the Framework genuinely does not say who must do what.

For every other catalog here, traceability and substance travel together:
if a sentence cites `[NIST 800-53 AC-2]` and says something, it says
something about account management, because AC-2 is an instruction. **Here
they come apart, and no check in this project currently distinguishes
them.** That is a known gap, stated rather than papered over.

The practical form: when reviewing generated content that cites this
catalog, ask *what would someone have to do differently tomorrow?* If the
answer is "nothing", the citation is decorative even though every
automated check passed.

### A document generated from an AI topic is an outcomes document

The five AI topics in `config/topics.example.yaml` anchor this catalog and
nothing else. A Standard generated from one of them therefore describes
**what good looks like**, and it commits nobody to a specific action. That
follows from the section above and is not a generation defect: the source
states outcomes, and PolicyForge does not decompose them into obligations,
because NIST publishes the actions separately, in the Playbook.

The obligations an AI programme draws on (governance, risk assessment,
secure development, training, third-party risk and incident response) are
800-53 controls. They are owned by the security topics in the same
registry, because every control has exactly one owning topic. **Which of
them serve which AI RMF outcome is your organisation's decision.**
PolicyForge does not make that pairing for you, for the reason the next
section gives.

### There is deliberately no crosswalk

`satisfies` resolves an AI RMF citation and then stops. That is not an
omission waiting to be fixed.

A crosswalk entry from `Govern 1.1` to some 800-53 control would assert
that implementing that control **achieves** the outcome. That is exactly
the claim NIST declined to make when it split the Playbook out of the
Framework and marked it voluntary. Publishing one here would put this
project's name on an assertion its source deliberately withheld.

If a crosswalk is ever added, it needs a defensible basis — NIST's own
published mapping, or a documented organisational decision — and it should
record which, per entry. `crosswalk seed` will happily generate the shape;
the shape is not the problem.

## Source

|          |                                                                      |
| -------- | -------------------------------------------------------------------- |
| Source   | NIST AIRC, `https://airc.nist.gov/airmf-resources/airmf/5-sec-core/` |
| Licence  | Public domain (a US government work)                                 |
| Revision | **1.0, pinned**                                                      |
| Command  | `policyforge etl-ai-rmf`                                             |

**AIRC, not CPRT.** CPRT does not publish the AI RMF Core. Both were
checked; this is recorded so it is not re-litigated.

**Pinned to Core revision 1.0.** The Playbook is revised more often than
the Framework, and a Playbook revision is *not* a reason to re-run the
ETL — nothing in this catalog comes from the Playbook.

## Parser notes

The parser keys on the **identifier's shape** (`Govern 1.1`), not on a CSS
class. Class names are AIRC's presentation and change without notice; the
numbering is the document's own grammar. A class-keyed parser would return
zero rows after a restyle and report success.

`parse_ai_rmf` raises rather than returning a short catalog, and it checks
structure rather than only emptiness:

- no rows at all,
- any row with empty text,
- an unrecognised function name,
- a subcategory whose parent category is missing,
- categories not contiguous from 1 within a function,
- any shape other than the one pinned under `shape:` in this catalog's
  `framework.yaml` — 19 and 72 for revision 1.0. More is refused as firmly
  as fewer, because contiguity accepts an invented `Govern 7`. The ETL never
  rewrites that key, so a new revision means editing it by hand, alongside
  this README.

The contiguity check is the one that earns its place: a partial parse that
drops `Govern 3` produces a well-formed catalog with a real count, and
looks exactly like success.

Ordering is function-major (`Govern 1…6`, then `Map`, `Measure`,
`Manage`). The first version sorted on the number alone and produced
`Govern 1, Map 1, Measure 1, Manage 1, Govern 2, …` — every row present,
every count correct, and an order the Framework does not have. Counting
rows says nothing about their arrangement.

# NIST AI RMF Playbook

**The Playbook is voluntary. A document that cites it may say NIST suggests an
action. It must never say NIST requires one.** In NIST's own words:

- "The AI RMF and the Playbook are intended for voluntary use."
  ([nist.gov](https://www.nist.gov/itl/ai-risk-management-framework/nist-ai-rmf-playbook))
- "The Playbook is neither a checklist nor set of steps to be followed in its
  entirety. Playbook suggestions are voluntary."
  ([AIRC](https://airc.nist.gov/airmf-resources/playbook/))
- "Playbook users are not expected to review or implement all of the
  suggestions or to go through it as an ordered series of steps."
  ([AIRC FAQ](https://airc.nist.gov/airmf-resources/playbook/faq/))

**How PolicyForge holds to this (#300).** The Standard and Procedure prompts
tell the model to write a Playbook-tagged action as NIST suggesting it, and to
put any requirement the organization adopts in a separate sentence without
the Playbook tag. `policyforge check` then reports, **as an error**, any
sentence that cites only the Playbook and is not framed as NIST's: its
subject must be NIST or the Playbook ("NIST suggests ...", "The Playbook
lists ..."), its verb must not be "requires", "mandates", "obliges" or
"directs", and it must not bind. **Anything else fails**, however it is
worded, so a new paraphrase of an obligation is caught by default. **No
heading may cite the Playbook**, not even in a merged tag: a heading's tag
covers every step beneath it, which turns a suggestion into an instruction by
position. `policyforge check` reports a Playbook-tagged heading as an error
too. Limits:

- A merged tag that also cites a binding source (`[NIST 800-53 CM-8 | NIST AI RMF Playbook ...]`) is held to that source's rule instead, so it may bind.
- The subject is read from the start of the sentence. A sentence about NIST
  with another subject, such as "One action NIST lists is ...", fails even
  though it asserts nothing; reword it with NIST or the Playbook as subject.
- An obligation the organization adopts, in its own sentence beside a
  Playbook citation, is reported by `policyforge check` as a binding
  sentence that cites nothing, for a person to confirm. It is seen only if
  it binds in the usual words ("must", "shall", "is required to"); one
  phrased "Acme Health requires ..." is not.
- A NIST-subject sentence that states an obligation in the passive, such as
  "NIST recommends that inventories are required for every system", passes:
  "are required for" is not one of the binding forms the check recognises.
- **Procedures.** A Procedure's steps are instructions, so a Playbook action
  under one reads as NIST's instruction wherever its tag sits. In a local
  test, a generated Procedure moved every Playbook tag onto its steps, and the
  check above reports every one. No bundled topic anchors Playbook actions
  yet. When the AI topics do (#301), the Procedure tier is to be left
  without them, citing its Standard and binding sources instead.

## What this catalog is

NIST's suggested actions for each of the 72 subcategories of the AI RMF 1.0
Core. The Core ([`nist-ai-rmf`](../nist-ai-rmf/README.md)) states *outcomes*,
such as "Legal and regulatory requirements involving AI are understood,
managed, and documented". NIST deliberately put the *actions* here instead, in
a separately versioned publication. This catalog is how a document can name an
action and attribute it to NIST without PolicyForge inventing it.

- **72 entries, one per Core subcategory**, each with the subcategory's id
  (`Govern 1.1`, `Map 3.4`, ...). Every entry serves a subcategory the Core
  has, and every Core subcategory has an entry. An entry for a subcategory the
  Core does not have is refused, not filed.
- **459 suggested actions**, cited as `[NIST AI RMF Playbook Govern 1.1 Action 3]`.
  **The action numbers are PolicyForge's**: the action's position in NIST's
  list. NIST does not number them.
- **The entry titles are PolicyForge's labels too**, such as "Suggested
  actions for GOVERN 1.1". **This catalog carries no outcome wording.** The
  outcome is read from `nist-ai-rmf` under the same id.

## Why the outcome wording is not carried

The Playbook export restates each subcategory's outcome. On the pinned export,
that wording **differs from the AI RMF 1.0 Core's in 30 of the 72
subcategories**, and in 8 more only in capitalisation (observed when this catalog was built, #177). For example:

| Subcategory | AI RMF 1.0 Core                                        | Playbook export                            |
| ----------- | ------------------------------------------------------ | ------------------------------------------ |
| Govern 1.2  | "...policies, processes, procedures, and practices."   | "...policies, processes, and procedures."  |
| Govern 1.3  | "Processes, procedures, and practices are in place..." | "Processes and procedures are in place..." |
| Govern 4.1  | "...to minimize potential negative impacts."           | "...to minimize negative impacts."         |

Carrying both would give one outcome two NIST-attributed wordings, each under
a valid citation. So the outcome stays in the Core, and this catalog links to
it by id. The actions are what this catalog is for, and they are carried as
the export states them.

## Pinned to one export

NIST publishes no revision number for the Playbook. Its FAQ says "there will
not be a 'final version'", and NIST re-publishes the export without logging
it. So the revision this catalog asserts is **the export itself**:

- `playbook.json` from AIRC, **SHA-256 `aecbee3d3c8820816d295b11d10fb61324b17c25c2ff39ee95e2aa5654555bba`**,
  served with `Last-Modified: 11 June 2026`. `policyforge etl-ai-rmf-playbook`
  **refuses any other export**, so a re-publication needs a person to review
  it and re-pin. On the scheduled drift job, that refusal is the job working.
  Do not "fix" the red.
- The Core is pinned separately (AI RMF 1.0). NIST has said the Playbook will
  be updated after the AI RMF is revised, so the two can drift, and each is
  checked against its own source.
- The number of actions per subcategory is pinned to a count taken
  independently of the parser. This project's research counted it two ways on #177:
  from the export's Markdown, and from NIST's rendered pages. A parse that is
  short or long is refused whole, never shipped partial.

## How the export was read

- An action is a **top-level item** of a subcategory's Suggested Actions list.
  Items nested under an action are part of its text.
- Two subcategories open with a lead-in sentence that the actions complete:
  Govern 1.2, "Organizational AI risk management policies should be designed
  to:", and Govern 3.1, "Organizational management can:". Each is kept as that
  entry's `discussion`, not prefixed to each action.
- **Manage 2.2's second action repeats itself** in the export, on a malformed
  line (`-Establish mechanisms...`, with no space after the dash). NIST's own
  page renders it as a continuation of the action above, and this catalog does
  the same. Counting it as a separate action gives 460, which is not the
  number of actions.

## Not yet

- **Topics do not anchor this catalog yet.** Letting the AI topics anchor
  Playbook entries, as well as the Core, is #301. It moves `/coverage` figures,
  so it lands separately.
- **There is no crosswalk.** A Playbook action is a voluntary suggestion that
  serves an outcome, not a control.

A US government work, so it is bundled and safe to commit to any repository.

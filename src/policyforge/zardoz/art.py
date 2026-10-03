"""The floating head, and every other line of Zardoz-flavoured chrome.

All of the persona lives in this one module, deliberately. Zardoz answers
questions about what an organization's security documents actually require,
and those answers have to read as plainly as the documents themselves — a
requirement someone pastes into a ticket or shows an assessor cannot arrive
wrapped in theatre. So the voice is confined to the *shell chrome*: the
launch banner, the help header, the goodbye, and the "I don't know that
word" line. Nothing here is ever mixed into a substantive answer.

Keeping it in one file is what makes that hold. If flavour strings were
sprinkled through the shell and the answering code, the line between chrome
and content would blur within a few commits; here the boundary is a module
import, and turning the persona off is one flag rather than a scavenger hunt.

The art is deliberately 7-bit ASCII. Block-drawing characters look sharper
in a modern terminal, but they render as mojibake in a classic `cmd.exe`
code page, and the first thing anyone sees on launch is a bad place to
discover an encoding problem. `HEAD` is a raw string so the backslashes
drawing the head's left cheek stay literal instead of becoming line
continuations.
"""

from __future__ import annotations

#: The stone head of Zardoz, floating. Pure ASCII, 57 columns at its widest,
#: which fits an 80-column terminal with room to spare.
HEAD = r"""
                 __________________________
             ,--'                          '--.
          ,-'                                  '-.
        ,'                                        ',
      ,'                                            ',
     /                                                \
    /       .-------.                    .-------.     \
   |       /         \                  /         \     |
   |      |    ,-.    |                |    ,-.    |    |
   |      |   (   )   |                |   (   )   |    |
   |      |    `-'    |                |    `-'    |    |
   |       \         /                  \         /     |
   |        `-------'                    `-------'      |
   |                                                    |
   |                         /\                         |
   |                        /  \                        |
   |                       /    \                       |
   |                      `------'                      |
   |                                                    |
   |                   .------------.                   |
   |                   `------------'                   |
    \                                                  /
     \           .--------------------------.          /
      ',        /  |  |  |  |  |  |  |  |  \         ,'
        '-.    /   |  |  |  |  |  |  |  |   \     ,-'
           '--'    |  |  |  |  |  |  |  |    '---'
                   |  |  |  |  |  |  |  |
                    \ |  |  |  |  |  | /
                     \|__|__|__|__|__|/
                      \  |  |  |  |  /
                       \_|__|__|__|_/
                         \ |  |  | /
                          `--------'

                    .   .   .   .   .   .
                      '   '   '   '   '
"""

#: Every line of persona, in one place. Keys are looked up from shell.py;
#: nothing constructs these strings inline.
VOICE = {
    "greeting": "ZARDOZ SPEAKS TO YOU, HIS CHOSEN ONE.",
    "subtitle": "The head knows only what your documents say. Ask it.",
    "help_header": "Zardoz permits:",
    "unknown_command": "Zardoz does not know this word: {command}",
    "goodbye": "Zardoz falls silent.",
    "interrupt": "(Zardoz is patient. Use /quit to send the head away.)",
    "command_failed": "That command failed. Zardoz remains; the session continues.",
}

#: The same keys with the theatre removed. A peer table rather than
#: conditionals scattered through the shell, so that turning the persona off
#: is a single lookup and cannot half-apply.
PLAIN_VOICE = {
    "greeting": "policyforge zardoz",
    "subtitle": "Answers come only from your synced documents. Ask a question.",
    "help_header": "Commands:",
    "unknown_command": "Unknown command: {command}",
    "goodbye": "Exiting.",
    "interrupt": "(Use /quit to exit.)",
    "command_failed": "That command failed; the session continues.",
}


def voice(*, plain: bool = False) -> dict[str, str]:
    """The chrome string table, with or without the persona."""
    return PLAIN_VOICE if plain else VOICE


def banner(*, art: bool = True, plain: bool = False) -> str:
    """The launch splash: the head, then the greeting.

    `art=False` keeps the greeting but drops the head — for a narrow
    terminal, or a captured log where thirty lines of stone face is noise.
    """
    lines = voice(plain=plain)
    parts = [HEAD.strip("\n"), ""] if art else []
    parts.append(f"  {lines['greeting']}")
    parts.append(f"  {lines['subtitle']}")
    parts.append("")
    parts.append("  Type /help for commands, /quit to leave.")
    return "\n".join(parts)

"""Passages sent as documents, so the API says what was cited.

Everywhere else in this project a passage is text inside a prompt, wrapped
in a per-request fence, and the citations that come back are `[2]` markers
the model wrote and `check_answer` verifies afterwards. That works, and the
verification is the only reason it works — a marker is a claim about a
passage, made by the same model that wrote the sentence, and a fabricated
one looks exactly like a real one until something checks it.

A document block is a different mechanism. The passage is a structured part
of the request rather than a region of a string, and each citation that
comes back carries the character range it came from and the text at that
range, *extracted by the API from the document* rather than generated. Two
things follow, and they are why this is worth a second code path:

* **A cited span is verbatim by construction.** Not checked to be verbatim —
  incapable of being otherwise. The most damaging output this tool can
  produce is a quotation that does not appear in the document it cites, and
  for these spans the failure mode is gone rather than caught.
* **The boundary stops being lexical.** `fence.py` exists because a passage
  and an instruction are the same kind of thing inside one string, so the
  split has to be drawn in the text and defended by a contract sentence that
  a page can imitate — which one live model did, three times out of three.
  A document block is not in the instruction's string at all. There is no
  marker to imitate because there is no marker.

What it does not give is a reason to stop checking. The model still chooses
*which* document to cite and still writes the sentence around the quote, so
it can still attach a real span to a claim the span does not support. The
citations are read here as evidence to cross-check the model's own markers
against, and `check_answer` runs exactly as before.

Citations cannot be combined with `output_config.format`: a reply cannot be
both constrained to a schema and cited. Nothing needs both — the answering
path is prose with markers — but a future caller reaching for `generate_json`
and `generate_grounded` at once will get an API error, and this is the note
that says why.

**`supports_grounding()` must mean the real Messages endpoint, not something
that looks like it.** Sent through OpenRouter's Anthropic-compatible proxy,
this exact request shape is accepted, the model reads the documents, and it
answers correctly — and the reply comes back with no citations at all. A
proxy that forwards document blocks and drops what they were for is
indistinguishable, from the caller's side, from a model that chose to quote
nothing. That is why the capability is advertised by the two providers that
hold a real client and by nothing else: a provider answering True here is
promising the cross-check exists, and one that returns prose with no spans
has quietly removed it while the caller believes it is still running.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Document:
    """One passage, as a block the request carries rather than text in it."""

    title: str
    text: str
    #: What the caller calls this document, carried through so a citation
    #: can be mapped back to it. The answering path uses the passage number
    #: the answer cites with, which is what makes a native citation and an
    #: `[n]` marker comparable at all.
    key: str = ""


@dataclass(frozen=True)
class Citation:
    """One span the API says was quoted, and where it came from."""

    #: Index into the documents as they were sent, zero-based.
    document_index: int
    #: The document's title as it was sent.
    document_title: str
    #: The text at the cited range, extracted from the document by the API.
    cited_text: str
    start_char: int | None = None
    end_char: int | None = None

    @property
    def key(self) -> str:
        """The caller's own name for the document, when it gave one."""
        return self.document_title


@dataclass
class Grounded:
    """A reply and the citations the API attached to it."""

    text: str
    citations: list[Citation] = field(default_factory=list)

    def cited_indices(self) -> list[int]:
        """Documents actually cited, zero-based, in the order first cited."""
        seen: list[int] = []
        for citation in self.citations:
            if citation.document_index not in seen:
                seen.append(citation.document_index)
        return seen


def documents_block(documents: list[Document]) -> list[dict]:
    """The document content blocks for a Messages API request.

    Titles are sent because a citation carries the title back, and a title
    is what makes the result legible when something goes wrong. The content
    is a plain text block per document: these are policy passages already
    extracted from Confluence storage format, not PDFs.
    """
    return [
        {
            "type": "document",
            "title": document.title,
            "source": {"type": "text", "media_type": "text/plain", "data": document.text},
            "citations": {"enabled": True},
        }
        for document in documents
    ]


def read_citations(response, documents: list[Document]) -> list[Citation]:
    """The citations on a response, flattened across its text blocks.

    Reads defensively through `getattr` rather than by attribute access:
    this walks SDK objects whose shape is the API's to change, and a
    citation that cannot be read should cost a cross-check rather than the
    answer it was attached to.
    """
    titles = [document.title for document in documents]
    found: list[Citation] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) != "text":
            continue
        for raw in getattr(block, "citations", None) or []:
            index = getattr(raw, "document_index", None)
            if not isinstance(index, int):
                continue
            found.append(
                Citation(
                    document_index=index,
                    document_title=(
                        getattr(raw, "document_title", None)
                        or (titles[index] if 0 <= index < len(titles) else "")
                    ),
                    cited_text=getattr(raw, "cited_text", "") or "",
                    start_char=getattr(raw, "start_char_index", None),
                    end_char=getattr(raw, "end_char_index", None),
                )
            )
    return found

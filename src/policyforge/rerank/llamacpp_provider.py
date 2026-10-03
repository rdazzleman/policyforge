"""Scores passages with a cross-encoder served by llama.cpp's `llama-server`.

Chosen because it needs nothing this project does not already have. A
machine running Ollama already has the binary — AMD's bundle ships it at
`lib/ollama/llama-server.exe` — and the reranker is one flag:

    llama-server -m bge-reranker-v2-m3-Q4_K_M.gguf --reranking --port 8090

That avoids the alternative, which is `sentence-transformers` and therefore
PyTorch: a large dependency with no ROCm build for Windows, so it would run
on the CPU anyway while costing gigabytes.

Two things learned setting this up, both worth not rediscovering:

- **`-hf` does not work on AMD's build.** It is compiled without SSL, so
  fetching from Hugging Face fails with "HTTPS is not supported" and the
  server then dies loading a model path of `''`. Download the GGUF with
  curl and pass `-m`.
- **`/health` returns OK while the model is still loading**, and calls made
  in that window come back `503 Loading model`. Loading takes a few seconds;
  `check()` below treats a 503 as "not ready" rather than as failure.

Every request goes through a `Channel`, like the embedder's: the question and
every candidate passage are checked against the boundary before they are
sent, and the batch is recorded in the ledger once it has been.
"""

from __future__ import annotations

from .base import Reranker, Scored

#: Where llama-server listens unless told otherwise, as the module docstring
#: starts it. Named so the factory classifies the URL this class calls.
DEFAULT_URL = "http://127.0.0.1:8090"


class LlamaCppReranker(Reranker):
    """Calls `POST {base_url}/v1/rerank` on a llama-server run with --reranking."""

    def __init__(
        self, *, base_url: str = DEFAULT_URL, timeout: int = 60, session=None, channel=None
    ):
        # Tolerate a trailing slash rather than producing a doubled one.
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        # As in OllamaEmbedder: built directly, it still gets a channel from
        # its own URL, so only a test injecting a session goes unguarded.
        if channel is None and session is None:
            from ..llm.channel import Channel

            channel = Channel.from_block(
                "rerank",
                {"base_url": base_url},
                provider="llamacpp",
                model="rerank",
                default_url=DEFAULT_URL,
            )
        self._channel = channel

        if session is not None:
            # Dependency injection point for tests, matching the LLM
            # providers, so the request/response handling is exercisable
            # with no server running.
            self._session = session
            return
        import requests

        from ..llm.boundary import is_loopback

        self._session = requests.Session()
        # The query and documents go to a loopback reranker directly, never
        # through a proxy the environment names (#69, measured: they went
        # through HTTP_PROXY in cleartext). A hosted endpoint keeps it.
        self._session.trust_env = not is_loopback(self.base_url)

    def score(self, query: str, documents: list[str]) -> list[Scored]:
        # The query is sent too, and it is often the most sensitive thing in
        # the request — a question about the organization's own gaps.
        texts = [query, *documents]
        if self._channel is not None:
            self._channel.admit()
        try:
            scored = self._post(query, documents)
        except Exception as exc:
            if self._channel is not None:
                self._channel.record(texts, error=type(exc).__name__)
            raise
        if self._channel is not None:
            self._channel.record(texts)
        return scored

    def _post(self, query: str, documents: list[str]) -> list[Scored]:
        import requests

        url = f"{self.base_url}/v1/rerank"
        payload = {"model": "rerank", "query": query, "documents": documents}
        try:
            response = self._session.post(url, json=payload, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            # "connection" is load-bearing here for the same reason it is in
            # OpenAICompatProvider: evals/runner.py grades a message
            # containing it as infrastructure rather than as a bad result.
            raise RuntimeError(
                f"Connection to {url} failed: {exc}. Is the reranker running? "
                f"Start it with: llama-server -m <reranker>.gguf --reranking --port 8090"
            ) from exc

        if response.status_code == 503:
            raise RuntimeError(
                f"{url} is still loading its model. /health reports OK before the "
                f"model is ready, so wait for the load line in the server's log."
            )
        if response.status_code != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status_code}: {response.text[:300]}")

        results = (response.json() or {}).get("results") or []
        # Sorted rather than trusted: the endpoint returns best-first today,
        # and an ordering this depends on is cheap to guarantee here and
        # expensive to debug if it ever changes.
        scored = [
            Scored(index=int(r["index"]), score=float(r["relevance_score"]))
            for r in results
            if "index" in r and "relevance_score" in r
        ]
        return sorted(scored, key=lambda s: s.score, reverse=True)

    def check(self) -> bool:
        """Whether the reranker can score a trivial pair right now.

        Sent past the channel, like the embedder's probe: fixed text, no
        content, and not worth a ledger line.
        """
        scored = self._post("access review cadence", ["Accounts are recertified quarterly."])
        return len(scored) == 1

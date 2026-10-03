"""Dense retrieval: finding passages that answer the question in other words."""

from .base import Embedder, cosine, get_embedder
from .dense import MIN_SIMILARITY, DenseIndex, build_dense_index, fuse

__all__ = [
    "MIN_SIMILARITY",
    "DenseIndex",
    "Embedder",
    "build_dense_index",
    "cosine",
    "fuse",
    "get_embedder",
]

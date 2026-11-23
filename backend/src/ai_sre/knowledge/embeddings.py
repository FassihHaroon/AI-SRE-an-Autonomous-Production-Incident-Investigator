"""Embedding models.

* :class:`FastEmbedEmbedder`: ``BAAI/bge-small-en-v1.5`` (384 dims) via fastembed / ONNX, run
  locally: no API calls, no tokens spent, works offline once the model is cached
  (``.cache/fastembed``). Queries use the model's retrieval instruction prefix.
* :class:`HashingEmbedder`: deterministic hashed bag-of-words. Not semantic; used by fast
  ingestion tests that only check mechanics.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

from ai_sre.config import REPO_ROOT
from ai_sre.knowledge import EMBEDDING_DIM

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    name = "BAAI/bge-small-en-v1.5"
    dim = EMBEDDING_DIM

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache = cache_dir or str(REPO_ROOT / ".cache" / "fastembed")
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.name, cache_dir=self._cache)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._load().embed(texts, batch_size=32)]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._load().query_embed(text)))]


class HashingEmbedder:
    name = "hashing-bow-384"
    dim = EMBEDDING_DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOKEN.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=4).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

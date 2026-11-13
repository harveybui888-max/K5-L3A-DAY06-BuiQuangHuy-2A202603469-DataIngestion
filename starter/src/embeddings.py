"""Local, real sentence embeddings shared by documents and queries."""

import math
import os
from pathlib import Path
from typing import Protocol

from fastembed import TextEmbedding


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text, in input order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with document vectors."""
        ...


class FastEmbedder:
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    ) -> None:
        self.model_name = model_name
        default_cache = Path.home() / ".cache" / "fastembed"
        self.cache_dir = os.environ.get("FASTEMBED_CACHE_PATH", str(default_cache))
        self.model = TextEmbedding(
            model_name=model_name,
            cache_dir=self.cache_dir,
            providers=["CPUExecutionProvider"],
        )
        probe = next(self.model.embed(["embedding dimension probe"]))
        self.dimensions = len(probe)
        if self.dimensions <= 0:
            raise RuntimeError(f"Embedding model did not report its dimension: {model_name}")

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for embedding in self.model.embed(texts, batch_size=32):
            vector = [float(value) for value in embedding]
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0:
                raise RuntimeError(f"Embedding model returned a zero vector: {self.model_name}")
            vectors.append([value / norm for value in vector])
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._encode(texts)
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding provider returned a different number of vectors")
        return vectors

    def embed_query(self, question: str) -> list[float]:
        vectors = self._encode([question])
        if len(vectors) != 1:
            raise RuntimeError("Embedding provider did not return one query vector")
        return vectors[0]

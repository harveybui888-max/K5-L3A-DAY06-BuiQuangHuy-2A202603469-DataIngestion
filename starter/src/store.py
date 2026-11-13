"""Persistent Chroma vector storage with cosine search and metadata filtering."""

from pathlib import Path
from typing import Any

import chromadb

from .models import Chunk


class ChromaVectorStore:
    def __init__(self, path: Path, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        path.mkdir(parents=True, exist_ok=True)
        self.dimensions = dimensions
        self.client = chromadb.PersistentClient(path=str(path))
        self.collections: dict[str, Any] = {}

    def _collection(self, strategy: str) -> Any:
        collection = self.collections.get(strategy)
        if collection is None:
            collection = self.client.get_or_create_collection(
                name=f"day06_{strategy.lower()}",
                metadata={"hnsw:space": "cosine"},
            )
            self.collections[strategy] = collection
        return collection

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"Expected {self.dimensions}-dimensional vectors, got {len(vector)}"
                )
        collection = self._collection(strategy)
        desired_ids = {chunk.chunk_id for chunk in chunks}
        existing = collection.get(include=["metadatas"])
        stale_ids = [
            chunk_id
            for chunk_id, metadata in zip(existing["ids"], existing["metadatas"])
            if chunk_id not in desired_ids
        ]
        if stale_ids:
            collection.delete(ids=stale_ids)
        if chunks:
            collection.upsert(
                ids=[chunk.chunk_id for chunk in chunks],
                documents=[chunk.text for chunk in chunks],
                embeddings=vectors,
                metadatas=[
                    {
                        **chunk.metadata,
                        "document_id": chunk.document_id,
                        "source": chunk.source,
                        "page": chunk.page,
                    }
                    for chunk in chunks
                ],
            )
        actual_count = collection.count()
        if actual_count != len(chunks):
            raise RuntimeError(
                f"{strategy} collection contains {actual_count} chunks, expected {len(chunks)}"
            )
        return actual_count

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if len(query_vector) != self.dimensions:
            raise ValueError(
                f"Expected a {self.dimensions}-dimensional query vector, got {len(query_vector)}"
            )
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        where = None
        if filters:
            allowed_fields = {"category", "document_id", "unit_id"}
            invalid_fields = set(filters) - allowed_fields
            if invalid_fields:
                raise ValueError(f"Unsupported metadata filter fields: {sorted(invalid_fields)}")
            where = {key: value for key, value in filters.items()}
        collection = self._collection(strategy)
        if collection.count() == 0:
            return []
        result = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = result["ids"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]
        return [
            {
                "chunk_id": chunk_id,
                "text": text,
                "metadata": metadata,
                "distance": float(distance),
                "score": 1.0 - float(distance),
            }
            for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances)
        ]

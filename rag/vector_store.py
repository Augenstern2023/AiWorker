# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from rag.schema import RagChunk, RetrievalResult


class VectorStoreError(RuntimeError):
    pass


class VectorStore:
    def add(self, chunks: list[RagChunk], vectors: list[list[float]]) -> None:
        raise NotImplementedError

    def query(self, vector: list[float], filters: dict[str, Any] | None = None, top_k: int = 8) -> list[RetrievalResult]:
        raise NotImplementedError

    def delete(self, job_id: str) -> None:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        raise NotImplementedError


class ChromaVectorStore(VectorStore):
    def __init__(self, persist_dir: str | Path = ".runtime/rag/chroma", collection_name: str = "aiworker_rag"):
        try:
            import chromadb
        except ImportError as exc:
            raise VectorStoreError("chromadb is not installed. Run `uv sync` after updating pyproject.toml.") from exc
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.collection = self.client.get_or_create_collection(collection_name)

    def add(self, chunks: list[RagChunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise VectorStoreError("chunks and vectors length mismatch.")
        if not chunks:
            return
        self.collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            embeddings=vectors,
            documents=[chunk.text for chunk in chunks],
            metadatas=[_flat_metadata(chunk) for chunk in chunks],
        )

    def query(self, vector: list[float], filters: dict[str, Any] | None = None, top_k: int = 8) -> list[RetrievalResult]:
        where = _chroma_where(filters or {})
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=max(1, top_k),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        rows = []
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for index, chunk_id in enumerate(ids):
            metadata = dict(metadatas[index] or {})
            chunk = _chunk_from_flat_metadata(chunk_id, documents[index], metadata)
            rows.append(RetrievalResult(chunk=chunk, score=1.0 / (1.0 + float(distances[index] or 0.0)), metadata=metadata))
        return rows

    def delete(self, job_id: str) -> None:
        self.collection.delete(where={"job_id": job_id})

    def status(self) -> dict[str, Any]:
        return {"backend": "chroma", "persist_dir": str(self.persist_dir), "chunk_count": self.collection.count()}


class JsonVectorStore(VectorStore):
    def __init__(self, path: str | Path = ".runtime/rag/local_vectors.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, chunks: list[RagChunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise VectorStoreError("chunks and vectors length mismatch.")
        data = self._read()
        for chunk, vector in zip(chunks, vectors):
            data[chunk.chunk_id] = {"chunk": chunk.model_dump(), "vector": vector}
        self._write(data)

    def query(self, vector: list[float], filters: dict[str, Any] | None = None, top_k: int = 8) -> list[RetrievalResult]:
        filters = filters or {}
        rows = []
        for item in self._read().values():
            chunk = RagChunk.model_validate(item["chunk"])
            if not _matches_filters(chunk, filters):
                continue
            score = _cosine(vector, item.get("vector") or [])
            rows.append(RetrievalResult(chunk=chunk, score=score, metadata=chunk.metadata))
        return sorted(rows, key=lambda item: item.score, reverse=True)[:top_k]

    def delete(self, job_id: str) -> None:
        self._write({key: value for key, value in self._read().items() if value.get("chunk", {}).get("job_id") != job_id})

    def status(self) -> dict[str, Any]:
        return {"backend": "json", "path": str(self.path), "chunk_count": len(self._read())}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_vector_store(runtime_dir: str | Path = ".runtime") -> VectorStore:
    persist_dir = Path(runtime_dir) / "rag" / "chroma"
    try:
        return ChromaVectorStore(persist_dir=persist_dir)
    except VectorStoreError:
        return JsonVectorStore(Path(runtime_dir) / "rag" / "local_vectors.json")


def _flat_metadata(chunk: RagChunk) -> dict[str, Any]:
    metadata = {
        "job_id": chunk.job_id,
        "platform": chunk.platform,
        "source_path": chunk.source_path,
        "source_type": chunk.source_type,
        "source_id": chunk.source_id,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
    }
    for key, value in chunk.metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            metadata[key] = value
        else:
            metadata[key] = json.dumps(value, ensure_ascii=False, default=str)
    return metadata


def _chunk_from_flat_metadata(chunk_id: str, text: str, metadata: dict[str, Any]) -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        job_id=str(metadata.get("job_id", "")),
        platform=str(metadata.get("platform", "")),
        source_path=str(metadata.get("source_path", "")),
        source_type=str(metadata.get("source_type", "")),
        source_id=str(metadata.get("source_id", "")),
        chunk_index=int(metadata.get("chunk_index") or 0),
        text=text,
        metadata={key: value for key, value in metadata.items() if key not in {"text"}},
    )


def _chroma_where(filters: dict[str, Any]) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = []
    for key in ("job_id", "platform", "source_type"):
        value = filters.get(key)
        if isinstance(value, list):
            if value:
                clauses.append({key: {"$in": [str(item) for item in value]}})
        elif value:
            clauses.append({key: str(value)})
    if len(clauses) == 1:
        return clauses[0]
    if len(clauses) > 1:
        return {"$and": clauses}
    return {}


def _matches_filters(chunk: RagChunk, filters: dict[str, Any]) -> bool:
    for key in ("job_id", "platform", "source_type"):
        expected = filters.get(key)
        if expected in (None, "", []):
            continue
        actual = getattr(chunk, key)
        if isinstance(expected, list):
            if actual not in [str(item) for item in expected]:
                return False
        elif actual != str(expected):
            return False
    return True


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(length))
    left_norm = math.sqrt(sum(value * value for value in left[:length]))
    right_norm = math.sqrt(sum(value * value for value in right[:length]))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)

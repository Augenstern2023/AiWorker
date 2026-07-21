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
from pathlib import Path

from rag.chunker import chunk_many
from rag.embeddings import EmbeddingProvider, default_embedding_provider
from rag.loaders import load_job_artifacts
from rag.normalizer import normalize
from rag.schema import IndexResult, utc_now_iso
from rag.vector_store import VectorStore, default_vector_store


class RagIndexer:
    def __init__(
        self,
        root: str | Path = ".",
        runtime_dir: str | Path = ".runtime",
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
    ):
        self.root = Path(root)
        self.runtime_dir = Path(runtime_dir)
        self.status_path = self.runtime_dir / "rag" / "index_status.json"
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_provider = embedding_provider or default_embedding_provider()
        self.vector_store = vector_store or default_vector_store(self.runtime_dir)

    async def index_job(self, job_id: str, platform: str, rebuild: bool = False) -> IndexResult:
        if rebuild:
            self.vector_store.delete(job_id)
        artifacts = load_job_artifacts(job_id=job_id, platform=platform, root=self.root)
        documents = []
        skipped: list[str] = []
        errors: list[str] = []
        for artifact in artifacts:
            try:
                documents.extend(normalize(artifact, platform))
            except Exception as exc:
                errors.append(f"{artifact.path}: {exc}")
        chunks = chunk_many(documents)
        if chunks:
            vectors = await self.embedding_provider.embed([chunk.text for chunk in chunks])
            self.vector_store.add(chunks, vectors)
        if not artifacts:
            skipped.append(f"data/{platform}/jobs/{job_id}")
        result = IndexResult(
            status="success" if not errors else "partial",
            job_id=job_id,
            platform=platform,
            artifact_count=len(artifacts),
            document_count=len(documents),
            chunk_count=len(chunks),
            skipped=skipped,
            errors=errors,
        )
        self._record_status(result)
        return result

    async def reindex_job(self, job_id: str, platform: str) -> IndexResult:
        return await self.index_job(job_id=job_id, platform=platform, rebuild=True)

    def get_index_status(self) -> dict[str, object]:
        state = self._read_status()
        vector_status = self.vector_store.status()
        jobs = state.get("jobs", {}) if isinstance(state.get("jobs"), dict) else {}
        return {
            "indexed_jobs": len(jobs),
            "jobs": jobs,
            "vector_store": vector_status,
            "chunk_count": vector_status.get("chunk_count", 0),
            "last_indexed_at": state.get("last_indexed_at", ""),
            "recent_errors": state.get("recent_errors", []),
        }

    def _record_status(self, result: IndexResult) -> None:
        state = self._read_status()
        jobs = state.setdefault("jobs", {})
        jobs[result.job_id] = result.model_dump()
        state["last_indexed_at"] = result.indexed_at
        if result.errors:
            recent_errors = list(state.get("recent_errors", []))
            recent_errors.extend(result.errors)
            state["recent_errors"] = recent_errors[-20:]
        state["updated_at"] = utc_now_iso()
        self.status_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_status(self) -> dict[str, object]:
        if not self.status_path.exists():
            return {"jobs": {}, "recent_errors": []}
        return json.loads(self.status_path.read_text(encoding="utf-8") or "{}")


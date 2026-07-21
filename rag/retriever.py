# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from rag.embeddings import EmbeddingProvider, default_embedding_provider
from rag.schema import RetrievalResult
from rag.vector_store import VectorStore, default_vector_store


class RagRetriever:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        runtime_dir: str = ".runtime",
    ):
        self.embedding_provider = embedding_provider or default_embedding_provider()
        self.vector_store = vector_store or default_vector_store(runtime_dir)

    async def retrieve(
        self,
        query: str,
        filters: dict[str, object] | None = None,
        top_k: int = 8,
    ) -> list[RetrievalResult]:
        vectors = await self.embedding_provider.embed([query])
        return self.vector_store.query(vectors[0], filters=filters or {}, top_k=top_k)


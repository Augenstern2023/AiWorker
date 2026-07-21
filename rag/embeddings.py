# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
from typing import Any

import httpx

import config


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.base_url:
            raise EmbeddingError("RAG_EMBEDDING_BASE_URL or LLM_BASE_URL is required.")
        if not self.api_key:
            raise EmbeddingError("RAG_EMBEDDING_API_KEY or LLM_API_KEY is required.")
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/embeddings", headers=headers, json=payload)
        response.raise_for_status()
        rows = response.json().get("data") or []
        vectors = [row.get("embedding") for row in sorted(rows, key=lambda item: item.get("index", 0))]
        if len(vectors) != len(texts) or any(not isinstance(vector, list) for vector in vectors):
            raise EmbeddingError("Embedding response shape is invalid.")
        return [[float(value) for value in vector] for vector in vectors]


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic local provider for tests and explicit offline demos."""

    def __init__(self, dimensions: int = 64):
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [item for item in text.lower().split() if item] or list(text)
        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]


def default_embedding_provider() -> EmbeddingProvider:
    provider = str(getattr(config, "RAG_EMBEDDING_PROVIDER", "openai-compatible")).lower()
    if provider == "hash":
        return HashEmbeddingProvider(dimensions=int(getattr(config, "RAG_HASH_EMBEDDING_DIMENSIONS", 64)))
    base_url = getattr(config, "RAG_EMBEDDING_BASE_URL", "")
    api_key = getattr(config, "RAG_EMBEDDING_API_KEY", "")
    if not base_url or not api_key:
        return HashEmbeddingProvider(dimensions=int(getattr(config, "RAG_HASH_EMBEDDING_DIMENSIONS", 64)))
    return OpenAICompatibleEmbeddingProvider(
        base_url=base_url,
        api_key=api_key,
        model=getattr(config, "RAG_EMBEDDING_MODEL", "text-embedding-3-small"),
    )

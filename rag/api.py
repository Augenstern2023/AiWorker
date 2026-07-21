# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from rag.embeddings import default_embedding_provider
from rag.indexer import RagIndexer
from rag.query_service import RagQueryService
from rag.retriever import RagRetriever
from rag.vector_store import default_vector_store


class RagIndexRequest(BaseModel):
    job_id: str
    platform: str
    rebuild: bool = False


class RagQueryRequest(BaseModel):
    question: str
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = 8


def build_rag_router(root: str | Path = ".", runtime_dir: str | Path = ".runtime") -> APIRouter:
    router = APIRouter(prefix="/api/rag", tags=["rag"])
    services: dict[str, Any] = {}

    def get_services() -> tuple[RagIndexer, RagQueryService]:
        if not services:
            embedding_provider = default_embedding_provider()
            vector_store = default_vector_store(runtime_dir)
            indexer = RagIndexer(
                root=root,
                runtime_dir=runtime_dir,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
            )
            retriever = RagRetriever(embedding_provider=embedding_provider, vector_store=vector_store)
            services["indexer"] = indexer
            services["query_service"] = RagQueryService(retriever=retriever)
        return services["indexer"], services["query_service"]

    @router.post("/index")
    async def index_job(payload: RagIndexRequest):
        if payload.platform not in {"dy", "hg"}:
            raise HTTPException(status_code=400, detail="platform must be dy or hg")
        try:
            indexer, _ = get_services()
            result = await indexer.index_job(payload.job_id, payload.platform, rebuild=payload.rebuild)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result.model_dump()

    @router.post("/query")
    async def query(payload: RagQueryRequest):
        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        top_k = min(max(payload.top_k, 1), 20)
        try:
            _, query_service = get_services()
            result = await query_service.query(question=question, filters=payload.filters, top_k=top_k)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result.model_dump()

    @router.get("/status")
    async def status():
        indexer, _ = get_services()
        return indexer.get_index_status()

    return router


def register_rag_routes(app: FastAPI, root: str | Path = ".", runtime_dir: str | Path = ".runtime") -> None:
    app.include_router(build_rag_router(root=root, runtime_dir=runtime_dir))

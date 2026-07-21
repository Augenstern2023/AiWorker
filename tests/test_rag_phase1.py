# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag.api import build_rag_router
from rag.chunker import chunk_many
from rag.embeddings import HashEmbeddingProvider, default_embedding_provider
from rag.indexer import RagIndexer
from rag.loaders import load_job_artifacts
from rag.normalizer import normalize
from rag.query_service import RagQueryService
from rag.retriever import RagRetriever
from rag.schema import Drama, RagChunk
from rag.vector_store import JsonVectorStore, _chroma_where


def test_rag_schema_models_are_serializable():
    drama = Drama(title="复仇之路", platform="dy", job_id="job-1", rank=1)
    chunk = RagChunk(
        chunk_id="chunk-1",
        job_id="job-1",
        platform="dy",
        source_path="data/dy/jobs/job-1/rank/rank.json",
        source_type="rank",
        source_id="doc-1",
        chunk_index=0,
        text="复仇之路 排名第一",
    )

    assert drama.model_dump()["title"] == "复仇之路"
    assert json.loads(chunk.model_dump_json())["job_id"] == "job-1"


def test_loader_normalizer_and_chunker_read_dy_job(tmp_path):
    _write_dy_job(tmp_path)

    artifacts = load_job_artifacts("job-1", "dy", root=tmp_path)
    documents = [document for artifact in artifacts for document in normalize(artifact, "dy")]
    chunks = chunk_many(documents)

    assert len(artifacts) == 2
    assert any(document.title == "复仇之路" for document in documents)
    assert chunks
    assert chunks[0].metadata["title"]


def test_default_embedding_provider_does_not_reuse_chat_llm_config(monkeypatch):
    monkeypatch.setattr("config.RAG_EMBEDDING_PROVIDER", "openai-compatible", raising=False)
    monkeypatch.setattr("config.RAG_EMBEDDING_BASE_URL", "", raising=False)
    monkeypatch.setattr("config.RAG_EMBEDDING_API_KEY", "", raising=False)
    monkeypatch.setattr("config.LLM_BASE_URL", "https://chat-only.example/v1", raising=False)
    monkeypatch.setattr("config.LLM_API_KEY", "chat-key", raising=False)

    provider = default_embedding_provider()

    assert isinstance(provider, HashEmbeddingProvider)


def test_chroma_where_wraps_multiple_filters_with_and():
    where = _chroma_where({"job_id": ["job-1"], "platform": ["hg"]})

    assert where == {"$and": [{"job_id": {"$in": ["job-1"]}}, {"platform": {"$in": ["hg"]}}]}


@pytest.mark.asyncio
async def test_index_query_citations_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("config.RAG_LLM_BASE_URL", "", raising=False)
    monkeypatch.setattr("config.RAG_LLM_API_KEY", "", raising=False)
    monkeypatch.setattr("config.LLM_BASE_URL", "", raising=False)
    monkeypatch.setattr("config.LLM_API_KEY", "", raising=False)
    _write_dy_job(tmp_path)
    store = JsonVectorStore(tmp_path / ".runtime" / "rag" / "vectors.json")
    embeddings = HashEmbeddingProvider()
    indexer = RagIndexer(root=tmp_path, runtime_dir=tmp_path / ".runtime", embedding_provider=embeddings, vector_store=store)

    result = await indexer.index_job("job-1", "dy", rebuild=True)
    retriever = RagRetriever(embedding_provider=embeddings, vector_store=store)
    service = RagQueryService(retriever=retriever)
    answer = await service.query("复仇短剧", filters={"platform": ["dy"], "job_id": ["job-1"]}, top_k=3)

    assert result.chunk_count >= 2
    assert answer.citations
    assert answer.citations[0].job_id == "job-1"
    assert answer.degraded is True


def test_rag_api_index_query_and_status(tmp_path, monkeypatch):
    _write_dy_job(tmp_path)
    monkeypatch.setattr("rag.api.default_embedding_provider", lambda: HashEmbeddingProvider())
    monkeypatch.setattr("rag.api.default_vector_store", lambda runtime_dir=".runtime": JsonVectorStore(Path(runtime_dir) / "rag" / "vectors.json"))

    app = FastAPI()
    app.include_router(build_rag_router(root=tmp_path, runtime_dir=tmp_path / ".runtime"))
    client = TestClient(app)

    index_response = client.post("/api/rag/index", json={"job_id": "job-1", "platform": "dy", "rebuild": True})
    query_response = client.post("/api/rag/query", json={"question": "复仇短剧", "filters": {"platform": ["dy"]}, "top_k": 2})
    status_response = client.get("/api/rag/status")

    assert index_response.status_code == 200
    assert index_response.json()["chunk_count"] >= 2
    assert query_response.status_code == 200
    assert query_response.json()["citations"]
    assert status_response.json()["indexed_jobs"] == 1


def _write_dy_job(root: Path) -> None:
    rank_dir = root / "data" / "dy" / "jobs" / "job-1" / "rank"
    rank_dir.mkdir(parents=True)
    (rank_dir / "rank.json").write_text(
        json.dumps(
            {
                "platform": "dy",
                "records": [
                    {
                        "rank": 1,
                        "title": "复仇之路",
                        "rank_category": "短剧",
                        "tab": "热榜",
                        "description": "女主被背叛后逆袭复仇。",
                        "like_count": "1000",
                        "comments": [{"content": "复仇爽点很强"}],
                    },
                    {
                        "rank": 2,
                        "title": "田园春风",
                        "rank_category": "短剧",
                        "tab": "榜单",
                        "description": "家庭创业成长故事。",
                        "like_count": "500",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (rank_dir / "rank.md").write_text("# 抖音短剧榜单\n\n| 排名 | 剧名 |\n| --- | --- |\n| 1 | 复仇之路 |\n", encoding="utf-8")

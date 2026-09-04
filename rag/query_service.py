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
from typing import Any

import config
from analysis.llm import create_llm_provider_from_config
from rag.retriever import RagRetriever
from rag.schema import AnswerWithCitations, Citation, RetrievalResult


class RagQueryService:
    def __init__(self, retriever: RagRetriever | None = None):
        self.retriever = retriever or RagRetriever()

    async def query(
        self,
        question: str,
        filters: dict[str, Any] | None = None,
        top_k: int = 8,
    ) -> AnswerWithCitations:
        results = await self.retriever.retrieve(question, filters=filters or {}, top_k=top_k)
        citations = [_citation_from_result(result) for result in results]
        try:
            answer = await _call_llm(question, results)
            return AnswerWithCitations(answer=answer, citations=citations, retrieved_chunks=results)
        except Exception as exc:
            return AnswerWithCitations(
                answer=_fallback_answer(question, results),
                citations=citations,
                retrieved_chunks=results,
                degraded=True,
                error=str(exc),
            )


def _citation_from_result(result: RetrievalResult) -> Citation:
    chunk = result.chunk
    return Citation(
        job_id=chunk.job_id,
        platform=chunk.platform,
        source_path=chunk.source_path,
        source_type=chunk.source_type,
        chunk_index=chunk.chunk_index,
        record_id=str(chunk.metadata.get("record_id", "")),
        title=str(chunk.metadata.get("title", "")),
        rank=chunk.metadata.get("rank") if isinstance(chunk.metadata.get("rank"), int) else None,
        score=result.score,
    )


async def _call_llm(question: str, results: list[RetrievalResult]) -> str:
    context = "\n\n".join(
        f"[{index + 1}] {item.chunk.metadata.get('title', '')} "
        f"({item.chunk.platform}/{item.chunk.job_id}/{item.chunk.source_type}#{item.chunk.chunk_index})\n{item.chunk.text}"
        for index, item in enumerate(results)
    )
    provider = create_llm_provider_from_config(config, prefix="RAG_LLM")
    return (
        await provider.complete_text(
            [
                {
                    "role": "system",
                    "content": "你是爬虫数据分析助手。只根据给定上下文回答，并在句末用 [1] 这样的编号标注来源。",
                },
                {
                    "role": "user",
                    "content": json.dumps({"question": question, "context": context}, ensure_ascii=False),
                },
            ]
        )
    ).strip()

def _fallback_answer(question: str, results: list[RetrievalResult]) -> str:
    if not results:
        return f"没有检索到与“{question}”相关的已索引内容。"
    lines = [f"已检索到与“{question}”相关的内容，当前未调用 LLM，先返回检索摘要："]
    for index, result in enumerate(results[:5], 1):
        title = result.chunk.metadata.get("title") or result.chunk.source_path
        snippet = result.chunk.text.replace("\n", " ")[:160]
        lines.append(f"{index}. {title}：{snippet}")
    return "\n".join(lines)

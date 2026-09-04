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
import re

from rag.schema import RagChunk, StandardDocument

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_OVERLAP = 120


def chunk(
    document: StandardDocument,
    strategy: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[RagChunk]:
    text = _normalize_text(document.text)
    if not text:
        return []
    size = _chunk_size_for(document.source_type, strategy, chunk_size)
    pieces = _split_text(text, size, overlap)
    return [
        RagChunk(
            chunk_id=_chunk_id(document, index, piece),
            job_id=document.job_id,
            platform=document.platform,
            source_path=document.source_path,
            source_type=document.source_type,
            source_id=document.document_id,
            chunk_index=index,
            text=piece,
            metadata={**document.metadata, "title": document.title, "strategy": strategy or document.source_type},
        )
        for index, piece in enumerate(pieces)
    ]


def chunk_many(documents: list[StandardDocument]) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    for document in documents:
        chunks.extend(chunk(document))
    return chunks


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    paragraphs = re.split(r"\n{2,}|(?<=。)", text)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_fixed_windows(paragraph, chunk_size, overlap))
            continue
        if len(current) + len(paragraph) + 1 > chunk_size:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap > 0 else ""
            current = f"{tail}\n{paragraph}".strip()
        else:
            current = f"{current}\n{paragraph}".strip() if current else paragraph
    if current:
        chunks.append(current.strip())
    return [item for item in chunks if item]


def _fixed_windows(text: str, chunk_size: int, overlap: int) -> list[str]:
    windows = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(text):
        windows.append(text[start:start + chunk_size].strip())
        start += step
    return [item for item in windows if item]


def _chunk_size_for(source_type: str, strategy: str | None, default: int) -> int:
    key = strategy or source_type
    if key in {"rank", "comment"}:
        return 900
    if key in {"asr", "analysis"}:
        return 1400
    return default


def _normalize_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text or "").strip()


def _chunk_id(document: StandardDocument, index: int, text: str) -> str:
    value = f"{document.document_id}:{index}:{text[:80]}"
    return hashlib.sha1(value.encode("utf-8")).hexdigest()

# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Long-lived model owner using only SemIf's published Python functions."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Any


SEMIF_UPSTREAM_COMMIT = "1f2dea3e25379f9dfc98cb83c324f00ab5deda37"
DEFAULT_MODEL_SOURCE = "Qwen/Qwen3.5-4B"
DEFAULT_MODEL_REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"


@dataclass(frozen=True)
class RuntimeSettings:
    gguf_path: Path
    gguf_sha256: str
    model_source: str = DEFAULT_MODEL_SOURCE
    model_revision: str = DEFAULT_MODEL_REVISION
    max_tokens: int = 4096
    threads: int | None = None

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        path = Path(os.getenv("SEMIF_GGUF_PATH", "")).expanduser()
        checksum = os.getenv("SEMIF_GGUF_SHA256", "").strip().lower()
        if not str(path) or str(path) == ".":
            raise ValueError("SEMIF_GGUF_PATH is required")
        if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
            raise ValueError("SEMIF_GGUF_SHA256 must be a 64-character SHA-256 digest")
        threads_text = os.getenv("SEMIF_LLAMA_THREADS", "").strip()
        threads = int(threads_text) if threads_text else None
        max_tokens = int(os.getenv("SEMIF_MAX_TOKENS", "4096"))
        if max_tokens < 256 or max_tokens > 4096:
            raise ValueError("SEMIF_MAX_TOKENS must be between 256 and 4096")
        return cls(
            gguf_path=path,
            gguf_sha256=checksum,
            model_source=os.getenv("SEMIF_MODEL_SOURCE", DEFAULT_MODEL_SOURCE),
            model_revision=os.getenv("SEMIF_MODEL_REVISION", DEFAULT_MODEL_REVISION),
            max_tokens=max_tokens,
            threads=threads,
        )


class SemIfRuntime:
    """Own one stateful llama.cpp context and serialize access to it."""

    backend = "llamacpp"

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.model: Any = None
        self.tokenizer: Any = None
        self.metadata: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        from semif_phase1.llamacpp_backend import load_model

        model, tokenizer, metadata = load_model(
            self.settings.model_source,
            self.settings.model_revision,
            self.settings.gguf_path,
            threads=self.settings.threads,
            context_tokens=self.settings.max_tokens,
        )
        actual = str((metadata.get("gguf") or {}).get("sha256") or "").lower()
        if actual != self.settings.gguf_sha256:
            model.close()
            raise ValueError(
                f"GGUF SHA-256 mismatch: expected {self.settings.gguf_sha256}, got {actual}"
            )
        self.model = model
        self.tokenizer = tokenizer
        self.metadata = metadata

    def close(self) -> None:
        if self.model is not None:
            self.model.close()
            self.model = None
            self.tokenizer = None

    def score(self, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not self.ready:
            raise RuntimeError("SemIf runtime is not ready")
        rows = [
            {
                "id": criterion["id"],
                "state": payload["state"],
                "question": criterion["question"],
                "options": criterion["options"],
            }
            for criterion in payload["criteria"]
        ]
        with self._lock:
            if payload["mode"] == "shared":
                from semif_phase1.llamacpp_backend import score_shared

                return score_shared(
                    self.model,
                    self.tokenizer,
                    rows,
                    self.metadata,
                    max_tokens=self.settings.max_tokens,
                )
            from semif_phase1.llamacpp_backend import score

            results = [
                score(
                    self.model,
                    self.tokenizer,
                    row,
                    self.metadata,
                    max_tokens=self.settings.max_tokens,
                )
                for row in rows
            ]
        return results, {
            "batch_size": len(results),
            "total_seconds": sum(float(item.get("total_seconds") or 0) for item in results),
        }

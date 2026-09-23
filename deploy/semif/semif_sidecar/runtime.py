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

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import site
import sys
import threading
from typing import Any


SEMIF_UPSTREAM_COMMIT = "1f2dea3e25379f9dfc98cb83c324f00ab5deda37"
DEFAULT_MODEL_SOURCE = "Qwen/Qwen3.5-4B"
DEFAULT_MODEL_REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
_CUDA_DLL_HANDLES: list[Any] = []


def _configure_cuda_dll_directories() -> None:
    """Make the pinned CUDA runtime wheels visible to native llama.cpp on Windows."""
    if os.name != "nt" or _CUDA_DLL_HANDLES:
        return
    candidates = []
    configured_library = os.getenv("LLAMA_CPP_LIB_PATH", "").strip()
    default_library = (
        Path(sys.prefix).resolve().parent / "native" / "llama-cpp-cu124-avx2"
    )
    llama_library = Path(configured_library) if configured_library else default_library
    if not llama_library.is_dir() and not configured_library:
        llama_library = next(
            (
                candidate
                for root in site.getsitepackages()
                if (candidate := Path(root) / "llama_cpp" / "lib").is_dir()
            ),
            None,
        )
    if llama_library is None:
        raise RuntimeError("llama.cpp native library directory is missing")
    if not llama_library.is_dir():
        raise RuntimeError(
            f"llama.cpp native library directory is missing: {llama_library}"
        )
    os.environ["LLAMA_CPP_LIB_PATH"] = str(llama_library)
    candidates.append(llama_library)
    for package in ("cuda_runtime", "cublas"):
        path = next(
            (
                candidate
                for root in site.getsitepackages()
                if (candidate := Path(root) / "nvidia" / package / "bin").is_dir()
            ),
            None,
        )
        if path is None:
            raise RuntimeError(f"CUDA runtime directory is missing for {package}")
        candidates.append(path)
    for path in candidates:
        _CUDA_DLL_HANDLES.append(os.add_dll_directory(str(path)))
    for library in (
        candidates[1] / "cudart64_12.dll",
        candidates[2] / "cublas64_12.dll",
        candidates[0] / "ggml-cuda.dll",
    ):
        _CUDA_DLL_HANDLES.append(ctypes.CDLL(str(library)))


@dataclass(frozen=True)
class RuntimeSettings:
    gguf_path: Path
    gguf_sha256: str
    model_source: str = DEFAULT_MODEL_SOURCE
    model_revision: str = DEFAULT_MODEL_REVISION
    max_tokens: int = 24_576
    threads: int | None = None
    gpu_layers: int = -1

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
        max_tokens = int(os.getenv("SEMIF_MAX_TOKENS", "24576"))
        if max_tokens < 256 or max_tokens > 32_768:
            raise ValueError("SEMIF_MAX_TOKENS must be between 256 and 32768")
        gpu_layers = int(os.getenv("SEMIF_LLAMA_GPU_LAYERS", "-1"))
        if gpu_layers < -1:
            raise ValueError("SEMIF_LLAMA_GPU_LAYERS must be -1, 0, or a positive integer")
        return cls(
            gguf_path=path,
            gguf_sha256=checksum,
            model_source=os.getenv("SEMIF_MODEL_SOURCE", DEFAULT_MODEL_SOURCE),
            model_revision=os.getenv("SEMIF_MODEL_REVISION", DEFAULT_MODEL_REVISION),
            max_tokens=max_tokens,
            threads=threads,
            gpu_layers=gpu_layers,
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
        from semif_phase1 import llamacpp_backend

        if self.settings.gpu_layers != 0:
            _configure_cuda_dll_directories()
            import llama_cpp

            supports_offload = getattr(llama_cpp, "llama_supports_gpu_offload", None)
            if supports_offload is None or not supports_offload():
                raise RuntimeError(
                    "The installed llama-cpp-python build does not support GPU offload"
                )

        original_model_params = llamacpp_backend._cpu_model_params

        def configured_model_params(library: Any) -> Any:
            params = original_model_params(library)
            params.n_gpu_layers = self.settings.gpu_layers
            return params

        llamacpp_backend._cpu_model_params = configured_model_params
        try:
            model, tokenizer, metadata = llamacpp_backend.load_model(
                self.settings.model_source,
                self.settings.model_revision,
                self.settings.gguf_path,
                threads=self.settings.threads,
                context_tokens=self.settings.max_tokens,
            )
        finally:
            llamacpp_backend._cpu_model_params = original_model_params

        metadata = {
            **metadata,
            "n_gpu_layers": self.settings.gpu_layers,
            "accelerator": "cuda" if self.settings.gpu_layers != 0 else "cpu",
        }
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

# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""FastAPI entry point exposing SemIf's real row-oriented scorer."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .runtime import RuntimeSettings, SEMIF_UPSTREAM_COMMIT, SemIfRuntime


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class Option(ContractModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Criterion(ContractModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[Option] = Field(min_length=2, max_length=16)


class ScoreRequest(ContractModel):
    schema_version: Literal["semif-score-v1"] = "semif-score-v1"
    request_id: str = Field(min_length=1)
    mode: Literal["direct", "shared"] = "shared"
    state: str | dict[str, Any] | list[Any]
    criteria: list[Criterion] = Field(min_length=1)
    state_refs: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_request(self) -> "ScoreRequest":
        if not self.state:
            raise ValueError("state must be nonempty")
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion IDs must be unique")
        for criterion in self.criteria:
            option_ids = [option.id for option in criterion.options]
            if len(option_ids) != len(set(option_ids)):
                raise ValueError(f"option IDs must be unique for {criterion.id}")
        return self


def create_app(runtime: Any | None = None) -> FastAPI:
    holder: dict[str, Any] = {"runtime": runtime}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        active = holder["runtime"]
        if active is None:
            active = SemIfRuntime(RuntimeSettings.from_environment())
            holder["runtime"] = active
        if not active.ready:
            await asyncio.to_thread(active.load)
        try:
            yield
        finally:
            if runtime is None:
                await asyncio.to_thread(active.close)

    app = FastAPI(title="AIWorker SemIf Sidecar", version="1", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        active = holder["runtime"]
        if active is None or not active.ready:
            raise HTTPException(status_code=503, detail="SemIf runtime is not ready")
        return {
            "status": "ok",
            "upstream_commit": SEMIF_UPSTREAM_COMMIT,
            "backend": active.backend,
            "model": active.metadata,
        }

    @app.post("/v1/score")
    async def score(request: ScoreRequest) -> dict[str, Any]:
        active = holder["runtime"]
        if active is None or not active.ready:
            raise HTTPException(status_code=503, detail="SemIf runtime is not ready")
        try:
            scores, timing = await asyncio.to_thread(
                active.score,
                request.model_dump(mode="json"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "schema_version": "semif-score-v1",
            "request_id": request.request_id,
            "mode": request.mode,
            "upstream_commit": SEMIF_UPSTREAM_COMMIT,
            "scores": scores,
            "timing": timing,
        }

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "semif_sidecar.app:app",
        host="127.0.0.1",
        port=8765,
        workers=1,
    )

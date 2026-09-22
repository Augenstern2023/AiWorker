# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from fastapi.testclient import TestClient

from deploy.semif.semif_sidecar.app import create_app
from deploy.semif.semif_sidecar.runtime import SEMIF_UPSTREAM_COMMIT


class FakeRuntime:
    ready = True
    backend = "fake"
    metadata = {"revision": "test-model"}

    def score(self, payload):
        results = []
        for criterion in payload["criteria"]:
            count = len(criterion["options"])
            results.append(
                {
                    "id": criterion["id"],
                    "option_ids": [item["id"] for item in criterion["options"]],
                    "probabilities": [1.0] + [0.0] * (count - 1),
                    "option_logits": [1.0] + [0.0] * (count - 1),
                    "input_tokens": 10,
                    "prompt_sha256": "a" * 64,
                    "prompt_version": "test-v1",
                    "model": self.metadata,
                    "readout": "test logits",
                    "probability_status": "conditional option score",
                }
            )
        return results, {"batch_size": len(results)}


def test_sidecar_exposes_health_and_generic_score_contract() -> None:
    app = create_app(FakeRuntime())
    with TestClient(app) as client:
        health = client.get("/health")
        response = client.post(
            "/v1/score",
            json={
                "schema_version": "semif-score-v1",
                "request_id": "request-1",
                "mode": "shared",
                "state": {"script": "主角反击"},
                "state_refs": [{"episode_number": 1}],
                "criteria": [
                    {
                        "id": "logic",
                        "question": "逻辑是否成立？",
                        "options": [
                            {"id": "yes", "description": "逻辑成立。"},
                            {"id": "no", "description": "逻辑不成立。"},
                        ],
                    }
                ],
            },
        )

    assert health.status_code == 200
    assert health.json()["upstream_commit"] == SEMIF_UPSTREAM_COMMIT
    assert response.status_code == 200
    assert response.json()["scores"][0]["option_ids"] == ["yes", "no"]


def test_sidecar_rejects_duplicate_criterion_ids() -> None:
    criterion = {
        "id": "logic",
        "question": "逻辑是否成立？",
        "options": [
            {"id": "yes", "description": "成立。"},
            {"id": "no", "description": "不成立。"},
        ],
    }
    app = create_app(FakeRuntime())
    with TestClient(app) as client:
        response = client.post(
            "/v1/score",
            json={
                "request_id": "request-1",
                "state": "剧本",
                "criteria": [criterion, criterion],
            },
        )

    assert response.status_code == 422

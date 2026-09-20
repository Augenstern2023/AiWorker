# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from creative.application_service import CreativeApplicationService


def test_failed_free_generation_reuses_operation_without_workflow_task() -> None:
    service = CreativeApplicationService.__new__(CreativeApplicationService)
    service.repository = SimpleNamespace(
        get_project=lambda project_id: {
            "id": project_id,
            "workflow_mode": "free",
        }
    )
    service.job_manager = SimpleNamespace(get_or_create_job=Mock(return_value="job"))
    service.assert_stage_write_access = Mock()
    actor = SimpleNamespace(user_id="writer-1", is_system_admin=False)
    payload = {
        "platform": "creative",
        "crawler_type": "phase4_generate",
        "extra": {
            "action": "script",
            "normalized_stage": "script",
            "workflow_mode": "free",
            "project_id": 9,
            "operation_id": "free-operation-retry",
            "generation_request_hash": "request-hash",
            "created_by_user_id": "writer-1",
        },
    }

    result = service._retry_free_generation_job(
        payload=payload,
        extra=payload["extra"],
        actor=actor,
    )

    assert result == "job"
    retry_payload = service.job_manager.get_or_create_job.call_args.args[1]
    assert retry_payload["extra"]["retry_failed_operation"] is True
    service.job_manager.get_or_create_job.assert_called_once_with(
        "free-operation-retry",
        retry_payload,
        created_by_user_id="writer-1",
        workflow_task_id=None,
        project_id=9,
    )

# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from creative.repository import CreativeRepository
from workflow.repository import WorkflowRepository


def migrate_legacy_workflow_project_identities(
    creative_repository: CreativeRepository,
    workflow_repository: WorkflowRepository,
) -> int:
    """Scope legacy numeric workflow rows for projects in this creative store."""
    migrated = 0
    for project_id in creative_repository.list_active_project_ids():
        migrated += int(
            workflow_repository.migrate_legacy_project_identity(
                str(project_id), creative_repository.workflow_project_id(project_id)
            )
        )
    return migrated

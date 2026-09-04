# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from creative.repository import CreativeRepository
from workflow.repository import WorkflowRepository


def migrate_project_creator_memberships(
    creative_repository: CreativeRepository,
    workflow_repository: WorkflowRepository,
) -> int:
    """Migrate only legacy owner memberships that match each project's creator."""
    return sum(
        workflow_repository.migrate_creator_membership(project_id, created_by)
        for project_id, created_by in creative_repository.list_project_creator_audit_rows()
    )

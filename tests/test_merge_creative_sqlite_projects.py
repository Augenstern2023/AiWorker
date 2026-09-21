# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import pytest

from creative.sqlite_import import CreativeImportError
from scripts import merge_creative_sqlite_projects as merge


def test_merge_rejects_project_creator_that_disagrees_with_created_by() -> None:
    project_rows = {
        8: {
            "id": 8,
            "name": "来源项目",
            "created_by": "expected-creator",
        }
    }
    memberships = {
        "creative:source-store:project:8": [
            {
                "user_id": "different-creator",
                "project_role": "project_creator",
                "status": "active",
            }
        ]
    }

    with pytest.raises(CreativeImportError, match="ownership"):
        merge._validate_project_ownership(project_rows, memberships)


def test_merge_accepts_one_matching_active_project_creator() -> None:
    project_rows = {
        8: {
            "id": 8,
            "name": "来源项目",
            "created_by": "expected-creator",
        }
    }
    memberships = {
        "creative:source-store:project:8": [
            {
                "user_id": "expected-creator",
                "project_role": "project_creator",
                "status": "active",
            },
            {
                "user_id": "writer",
                "project_role": "screenwriter",
                "status": "active",
            },
        ]
    }

    merge._validate_project_ownership(project_rows, memberships)

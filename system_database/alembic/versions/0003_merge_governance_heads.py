# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Merge the independently developed governance migrations."""

from __future__ import annotations


revision = "0003_merge_governance_heads"
down_revision = (
    "0002_organization_departments",
    "0002_audit_actor_snapshot",
    "0002_navigation_items",
    "0002_schedule_governance",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

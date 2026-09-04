# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Grant project deletion only to platform project owners."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_project_creator_governance"
down_revision = "0013_screenwriter_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO permissions (code, label) VALUES "
            "('project.delete', '删除项目') ON CONFLICT (code) DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code, data_scope) "
            "SELECT id, 'project.delete', 'managed_projects' FROM roles "
            "WHERE lower(code) = 'project_owner' AND is_deleted = 0 "
            "ON CONFLICT (role_id, permission_code) DO NOTHING"
        )
    )


def downgrade() -> None:
    # Authorization grants are administrator-owned configuration.
    pass

# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Backfill project creation grants for disabled built-in roles."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_project_creator_backfill"
down_revision = "0009_project_creator_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for role_code, data_scope in (
        ("screenwriter", "project"),
        ("project_owner", "managed_projects"),
    ):
        op.execute(
            sa.text(
                """
                INSERT INTO role_permissions
                    (role_id, permission_code, data_scope)
                SELECT id, 'project.create', :data_scope
                FROM roles
                WHERE lower(code) = :role_code
                  AND is_deleted = 0
                ON CONFLICT (role_id, permission_code) DO NOTHING
                """
            ).bindparams(role_code=role_code, data_scope=data_scope)
        )


def downgrade() -> None:
    # Role permissions are administrator-owned configuration.  Never remove a
    # grant that may have been configured independently of this migration.
    pass

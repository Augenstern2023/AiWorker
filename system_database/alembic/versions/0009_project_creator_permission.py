# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Grant the narrow project creation permission to creator roles."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_project_creator_permission"
down_revision = "0008_remove_schedule_governance"
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
                  AND is_enabled = 1
                  AND is_deleted = 0
                ON CONFLICT (role_id, permission_code) DO NOTHING
                """
            ).bindparams(role_code=role_code, data_scope=data_scope)
        )


def downgrade() -> None:
    # Permissions are administrator-owned configuration.  A downgrade must not
    # guess whether a grant was created here or configured independently.
    pass

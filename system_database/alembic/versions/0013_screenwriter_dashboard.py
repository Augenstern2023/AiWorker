# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Grant the dedicated writing dashboard to existing screenwriter roles."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_screenwriter_dashboard"
down_revision = "0012_writer_stage_edit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE navigation_items
            SET label = '选品概览', updated_at = CURRENT_TIMESTAMP
            WHERE id = 'navigation:selection:dashboard'
              AND label = '工作概览'
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_page_access (role_id, route)
            SELECT id, '/writing/dashboard'
            FROM roles
            WHERE lower(code) = 'screenwriter'
              AND is_deleted = 0
            ON CONFLICT (role_id, route) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Page grants are administrator-owned configuration; do not remove a grant
    # that may have been configured independently.
    pass

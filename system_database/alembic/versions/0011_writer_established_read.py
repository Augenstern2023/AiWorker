# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Allow screenwriters to read established projects without managing them."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_writer_established_read"
down_revision = "0010_project_creator_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO permissions (code, label)
            VALUES ('project.read.established', '查看已设立项目')
            ON CONFLICT (code) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions
                (role_id, permission_code, data_scope)
            SELECT id, 'project.read.established', 'project'
            FROM roles
            WHERE lower(code) = 'screenwriter'
              AND is_deleted = 0
            ON CONFLICT (role_id, permission_code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Authorization is administrator-owned configuration.  Never remove a
    # permission or grant that may have been configured independently.
    pass

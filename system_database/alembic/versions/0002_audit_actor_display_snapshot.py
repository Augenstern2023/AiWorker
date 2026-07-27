# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_audit_actor_snapshot"
down_revision = "0001_system_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("actor_display_name_snapshot", sa.Text))
    op.execute(
        """
        UPDATE audit_logs AS audit
        SET actor_display_name_snapshot = COALESCE(users.display_name, audit.actor_user_id)
        FROM users
        WHERE users.id = audit.actor_user_id
          AND audit.actor_display_name_snapshot IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "actor_display_name_snapshot")
